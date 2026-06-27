import datetime

from easysynq_api.db.models._capa_enums import NcSeverity
from easysynq_api.domain.capa.targets import CAPA_TARGET_DAYS, default_target_date


def test_offsets_by_severity():
    assert CAPA_TARGET_DAYS == {NcSeverity.Critical: 30, NcSeverity.Major: 60, NcSeverity.Minor: 90}


def test_default_target_date_adds_calendar_days():
    raised = datetime.date(2026, 6, 24)
    assert default_target_date(NcSeverity.Critical, raised) == datetime.date(2026, 7, 24)
    assert default_target_date(NcSeverity.Major, raised) == datetime.date(2026, 8, 23)
    assert default_target_date(NcSeverity.Minor, raised) == datetime.date(2026, 9, 22)
