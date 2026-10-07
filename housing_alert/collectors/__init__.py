"""출처별 수집기 레지스트리. 각 수집기는 독립적으로 실패할 수 있다."""
from __future__ import annotations

from .api import APPLYHOME_COLLECTORS, ApplyhomeCollector, KeyMissing, LHApiCollector
from .base import Collector, NoticeData
from .gh import GHApplyCollector, GHWwwCollector
from .lh import LHCollector
from .sh import SHCollector

__all__ = ["Collector", "NoticeData", "KeyMissing", "build_collectors", "SOURCE_LABELS"]


def build_collectors(key_getter=lambda: "") -> list[Collector]:
    collectors: list[Collector] = [LHCollector(), SHCollector(), GHApplyCollector(), GHWwwCollector()]
    for name, label, op, model_op, kind in APPLYHOME_COLLECTORS:
        c = ApplyhomeCollector(name, label, op, model_op, kind)
        c.key_getter = key_getter
        collectors.append(c)
    lh_api = LHApiCollector()
    lh_api.key_getter = key_getter
    collectors.append(lh_api)
    return collectors


SOURCE_LABELS = {c.name: c.label for c in build_collectors()}
# lh_api 결과는 source='lh'로 저장된다
SOURCE_LABELS.setdefault("lh", "LH 청약플러스")
