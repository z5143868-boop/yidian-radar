"""
调度与容错核心。

整个系统的容错都在这里：每个 collector 独立跑，独立失败，
任何一个挂掉都不影响其他源，也不影响周报出不出得来。

这是架构里最重要的一个文件——不是因为它复杂，
而是因为它决定了这个系统会不会在某个源失效后悄悄死掉。
"""

import time
import traceback
from datetime import datetime, timezone
from typing import Callable, Any


class SourceResult:
    """单个数据源的采集结果。失败也是一种结果，不是异常。"""

    def __init__(self, name: str):
        self.name = name
        self.status = "ok"          # ok | partial | failed
        self.data: Any = None
        self.error: str | None = None
        self.items_collected = 0

    def fail(self, err: Exception) -> "SourceResult":
        self.status = "failed"
        # 只留一行错误摘要，完整堆栈打到日志，不塞进 JSON
        self.error = f"{type(err).__name__}: {str(err)[:200]}"
        return self

    def partial(self, err: Exception) -> "SourceResult":
        self.status = "partial"
        self.error = f"{type(err).__name__}: {str(err)[:200]}"
        return self

    def to_health(self) -> str:
        if self.status == "ok":
            return "ok"
        if self.status == "partial":
            return f"partial: {self.error}"
        return f"failed: {self.error}"


class Runner:
    """
    按源跑 collector，收集结果，绝不让单个失败冒泡。

    用法：
        runner = Runner(delay=1.5)
        result = runner.run("apple", fetch_apple_data, competitor)
    """

    def __init__(self, delay: float = 1.5, retries: int = 2):
        self.delay = delay
        self.retries = retries
        self.results: dict[str, SourceResult] = {}
        self._last_call = 0.0

    def _throttle(self):
        """请求间隔。别把对方站点打疼了，也别把自己 IP 打进黑名单。"""
        elapsed = time.time() - self._last_call
        if elapsed < self.delay:
            time.sleep(self.delay - elapsed)
        self._last_call = time.time()

    def run(self, source_name: str, fn: Callable, *args,
            allow_empty: bool = False, **kwargs) -> SourceResult:
        """
        跑一个采集函数。无论发生什么都返回 SourceResult，绝不抛异常。

        这个「绝不抛异常」是刻意的。上层不需要写 try/except，
        也就不会有人图省事在外面包一个大 try 把所有错误吞掉。

        allow_empty=True 时空结果算正常。这个参数很重要：
        新闻源本周没有新闻是常态，不是故障。如果把它标成 partial，
        source_health 会长期一片橙色，而人对长期报警的反应是无视它——
        真正的故障就被淹没了。只有「应该有数据却没有」才算异常。
        """
        result = SourceResult(source_name)
        last_error = None

        # 重试是必需的，不是保险。实测 Apple 评论接口会偶发返回空 feed，
        # 同一个 URL 隔几秒重试就正常——第一次跑的时候被这个坑了一次。
        for attempt in range(self.retries + 1):
            self._throttle()
            try:
                data = fn(*args, **kwargs)

                if not data:
                    if allow_empty:
                        # 本周没新闻、没讨论——正常状态，不重试也不报警
                        result.data = data
                        result.status = "ok"
                        result.error = None
                        self.results[source_name] = result
                        return result

                    # 空结果可能是真没数据，也可能是限流。先退避重试，
                    # 重试完还是空才标 partial
                    if attempt < self.retries:
                        time.sleep(3 * (attempt + 1))
                        continue
                    result.data = data
                    result.status = "partial"
                    result.error = "returned empty after retries"
                    self.results[source_name] = result
                    return result

                result.data = data
                result.items_collected = len(data) if isinstance(data, (list, dict)) else 1
                self.results[source_name] = result
                return result

            except Exception as err:
                last_error = err
                if attempt < self.retries:
                    time.sleep(2 * (attempt + 1))
                    continue

        result.fail(last_error)
        print(f"  [FAIL] {source_name}: {result.error}")
        print(traceback.format_exc()[:300])

        self.results[source_name] = result
        return result

    def health_report(self) -> dict[str, str]:
        """汇总成 source_health 字段，直接进 JSON。"""
        return {name: r.to_health() for name, r in self.results.items()}

    def has_any_success(self) -> bool:
        """
        只要有一个源成功就出报告。

        这条规则是刻意的：宁可出一份标着「本周缺 3 个源」的残缺报告，
        也不要因为一个源挂了就整周静默。静默是这类系统真正的死因。
        """
        return any(r.status in ("ok", "partial") for r in self.results.values())

    def summary_line(self) -> str:
        ok = sum(1 for r in self.results.values() if r.status == "ok")
        partial = sum(1 for r in self.results.values() if r.status == "partial")
        failed = sum(1 for r in self.results.values() if r.status == "failed")
        return f"采集完成: {ok} 成功 / {partial} 部分 / {failed} 失败"


def current_week_id() -> str:
    """ISO 周编号，如 2026-W38。一周一个文件的文件名就是它。"""
    now = datetime.now(timezone.utc)
    year, week, _ = now.isocalendar()
    return f"{year}-W{week:02d}"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
