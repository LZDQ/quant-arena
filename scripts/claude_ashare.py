import asyncio
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Shanghai")
BASE_DIR = Path.home() / "ldq" / "ashare-arena"

AGENTS = {
    "arbitrage": {
        "workdir": BASE_DIR / "arbitrage",
        "jobs": [
            ("08:00", "现在是早上 8:00。你作为詹姆斯西蒙斯转世，即将续写新的传说。请按照指示完成初始化、制定今天策略。"),
            ("09:35", "现在是早盘 9:35，请按照计划套利。"),
            ("09:45", "现在是早盘 9:45，请按照计划套利。"),
            ("10:00", "现在是早盘 10:00，请按照计划继续套利、观察实盘并买入。"),
            ("10:10", "现在是早盘 10:10，请按照计划继续处理未完成的套利、买入和止损。"),
            ("11:00", "现在是早盘 11:00，请按照计划继续处理未完成的止损，宁可不赚也不能继续亏；同时关注明显有承接的回踩票吸低。"),
            ("14:00", "现在是午后 14:00，请按照计划继续盯盘。"),
            ("14:50", "现在是午后 14:50，请按照计划继续盯盘。如果出现炸板，尽快跑路。"),
            ("19:00", "现在是复盘时间。请按照计划阅读技能包并提交今日日报。"),
        ],
    },
    "buffett": {
        "workdir": BASE_DIR / "buffett",
        "jobs": [
            ("08:00", "现在是早上 8:00。你作为股神巴菲特转世，即将续写新的传说。请按照指示完成初始化、制定今天策略。"),
            ("10:00", "现在是早盘 10:00，请按照计划盯盘并操作。"),
            ("14:00", "现在是早盘 14:00，请按照计划盯盘并操作。"),
            ("19:00", "现在是复盘时间。请按照计划阅读技能包并提交今日日报。"),
        ],
    },
    "livermore": {
        "workdir": BASE_DIR / "livermore",
        "jobs": [
            ("08:00", "现在是早上 8:00。你作为杰西·利弗莫尔转世，即将续写新的传说。请按照指示完成初始化、制定今天策略。"),
            ("09:45", "现在是早盘 9:45，请按照计划盯盘、追涨、套利操作。"),
            ("10:00", "现在是早盘 10:00，请按照计划继续追涨，尽可能达到 80% 仓位。"),
            ("10:10", "现在是早盘 10:10，请按照计划继续处理未完成的订单。"),
            ("11:00", "现在是早盘 11:00，请按照计划继续处理未完成的订单、追上火箭。"),
            ("14:00", "现在是午后 14:00，请按照计划继续盯盘。"),
            ("14:50", "现在是午后 14:50，请按照计划继续盯盘。"),
            ("19:00", "现在是复盘时间。请按照计划阅读技能包并提交今日日报。"),
        ],
    },
    "daily-report-summary": {
        "workdir": BASE_DIR / "daily-report-summary",
        "jobs": [
            ("19:10", "现在是复盘时间。请按照计划对所有 agent 进行汇总并提交自己的汇总日报。"),
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
