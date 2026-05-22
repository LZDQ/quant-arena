import asyncio
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/New_York")
BASE_DIR = Path.home() / "ldq" / "US-market"

AGENTS = {
    "trump": {
        "workdir": BASE_DIR / "trump",
        "jobs": [
            ("08:00", "现在是早上 8:00。你作为股神特朗普，即将续写新的传说。请按照指示完成初始化、制定今天策略。"),
            ("10:00", "现在是早盘 10:00，请按照计划盯盘并操作。"),
            ("11:00", "现在是早盘 11:00，请按照计划盯盘并操作。"),
            ("14:00", "现在是早盘 14:00，请按照计划盯盘并操作。"),
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


async def run_claude(agent_name: str, workdir: Path, job_index: int, prompt: str) -> None:
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

    p = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(workdir),
    )
    rc = await p.wait()

    log(f"DONE agent={agent_name} job={job_index} exit={rc}")


async def agent_loop(agent_name: str, cfg: dict) -> None:
    workdir: Path = cfg["workdir"]
    jobs: list[tuple[str, str]] = cfg["jobs"]

    while True:
        n = now()
        await asyncio.sleep(60 - n.second - n.microsecond / 1_000_000)

        if not is_weekday():
            continue

        hhmm = now().strftime("%H:%M")
        for job_index, (job_time, prompt) in enumerate(jobs):
            if hhmm == job_time:
                await run_claude(agent_name, workdir, job_index, prompt)


async def main() -> None:
    log("scheduler started")
    await asyncio.gather(*(agent_loop(name, cfg) for name, cfg in AGENTS.items()))


if __name__ == "__main__":
    asyncio.run(main())
