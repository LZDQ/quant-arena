import subprocess
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Shanghai")

AGENTS = {
    "arbitrage": {
        "workdir": Path.home() / "ashare-arbitrage",
        "jobs": [
            ("08:00", "现在是早上 8:00，你作为股神转世，即将续写新的传说。请按照指示完成初始化、制定今天策略。"),
            ("09:35", "现在是早盘 9:35，请按照计划套利。"),
            ("09:45", "现在是早盘 9:45，请按照计划套利。"),
            ("10:00", "现在是早盘 10:00，请按照计划继续套利、观察实盘并买入。"),
            ("10:10", "现在是早盘 10:10，请按照计划继续处理未完成的套利、买入和止损。"),
            ("11:00", "现在是早盘 11:00，请按照计划继续处理未完成的止损，宁可不赚也不能继续亏；同时关注明显有承接的回踩票吸低。"),
            ("13:30", "现在是午后 13:30，请按照计划继续盯盘。"),
            ("14:00", "现在是午后 14:00，请按照计划继续盯盘。"),
            ("14:50", "现在是午后 14:50，请按照计划继续盯盘。如果出现炸板，尽快跑路。"),
            ("19:00", "现在是复盘时间。请按照计划阅读技能包并提交今日日报。"),
        ],
    },
}


def now() -> datetime:
    return datetime.now(TZ)


def today_str() -> str:
    return now().strftime("%Y-%m-%d")


def session_name(agent_name: str) -> str:
    return f"{agent_name}-{today_str()}"


def log(msg: str) -> None:
    print(f"[{now().isoformat(timespec='seconds')}] {msg}", flush=True)


def is_weekday() -> bool:
    return now().weekday() < 5


def run_claude(agent_name: str, workdir: Path, job_index: int, prompt: str) -> None:
    workdir.mkdir(parents=True, exist_ok=True)

    session = session_name(agent_name)

    if job_index == 0:
        cmd = [
            "claude", "-p",
            "--name", session,
            prompt,
        ]
    else:
        cmd = [
            "claude", "-p",
            "--resume", session,
            prompt,
        ]

    log(f"RUN agent={agent_name} job={job_index} session={session}")

    p = subprocess.run(
        cmd,
        cwd=workdir,
        text=True,
    )

    log(f"DONE agent={agent_name} job={job_index} exit={p.returncode}")


def main() -> None:
    ran: set[tuple[str, str, int]] = set()

    log("scheduler started")

    while True:
        current_day = today_str()

        if not is_weekday():
            time.sleep(60)
            continue

        hhmm = now().strftime("%H:%M")

        for agent_name, cfg in AGENTS.items():
            workdir: Path = cfg["workdir"]
            jobs: list[tuple[str, str]] = cfg["jobs"]

            for job_index, (job_time, prompt) in enumerate(jobs):
                key = (current_day, agent_name, job_index)

                if hhmm >= job_time and key not in ran:
                    run_claude(agent_name, workdir, job_index, prompt)
                    ran.add(key)

        ran = {x for x in ran if x[0] == current_day}

        time.sleep(20)


if __name__ == "__main__":
    main()
