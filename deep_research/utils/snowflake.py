"""
雪花算法 ID 生成器
生成 64 位唯一 ID：时间戳(41位) + 机器ID(10位) + 序列号(12位)
"""
import time
import threading


class SnowflakeIDGenerator:
    EPOCH = 1700000000000  # 自定义纪元（毫秒）
    WORKER_ID_BITS = 10
    SEQUENCE_BITS = 12

    MAX_WORKER_ID = (1 << WORKER_ID_BITS) - 1
    MAX_SEQUENCE = (1 << SEQUENCE_BITS) - 1

    WORKER_ID_SHIFT = SEQUENCE_BITS
    TIMESTAMP_SHIFT = SEQUENCE_BITS + WORKER_ID_BITS

    def __init__(self, worker_id: int = 1):
        if worker_id < 0 or worker_id > self.MAX_WORKER_ID:
            raise ValueError(f"worker_id 必须在 0 到 {self.MAX_WORKER_ID} 之间")
        self.worker_id = worker_id
        self.sequence = 0
        self.last_timestamp = -1
        self._lock = threading.Lock()

    def _current_ms(self) -> int:
        return int(time.time() * 1000)

    def _wait_next_ms(self, last_ts: int) -> int:
        ts = self._current_ms()
        while ts <= last_ts:
            ts = self._current_ms()
        return ts

    def next_id(self) -> int:
        with self._lock:
            ts = self._current_ms()
            if ts < self.last_timestamp:
                raise RuntimeError("系统时钟回拨，拒绝生成 ID")
            if ts == self.last_timestamp:
                self.sequence = (self.sequence + 1) & self.MAX_SEQUENCE
                if self.sequence == 0:
                    ts = self._wait_next_ms(self.last_timestamp)
            else:
                self.sequence = 0
            self.last_timestamp = ts
            return (
                ((ts - self.EPOCH) << self.TIMESTAMP_SHIFT)
                | (self.worker_id << self.WORKER_ID_SHIFT)
                | self.sequence
            )


_default_generator = SnowflakeIDGenerator(worker_id=1)


def generate_id() -> str:
    """生成雪花算法 ID，返回字符串形式"""
    return str(_default_generator.next_id())
