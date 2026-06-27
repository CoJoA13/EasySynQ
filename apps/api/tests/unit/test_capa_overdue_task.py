from easysynq_api.tasks.app import app


def test_capa_overdue_sweep_task_registered():
    assert "easysynq.capa.overdue_sweep" in app.tasks
