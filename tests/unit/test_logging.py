import json
import logging
import sys

from assessments.logging import JsonFormatter, request_id_var, run_id_var


def make_record(**extra: object) -> logging.LogRecord:
    record = logging.LogRecord(
        "test.logger", logging.INFO, __file__, 1, "hello %s", ("world",), None
    )
    record.__dict__.update(extra)
    return record


def test_formats_record_as_json_with_extra_fields() -> None:
    line = JsonFormatter().format(make_record(step=3, action="click"))

    payload = json.loads(line)
    assert payload["msg"] == "hello world"
    assert payload["level"] == "INFO"
    assert payload["logger"] == "test.logger"
    assert payload["step"] == 3
    assert payload["action"] == "click"
    assert "args" not in payload


def test_includes_correlation_ids_from_context() -> None:
    request_token = request_id_var.set("req-1")
    run_token = run_id_var.set("run-1")
    try:
        payload = json.loads(JsonFormatter().format(make_record()))
    finally:
        request_id_var.reset(request_token)
        run_id_var.reset(run_token)

    assert payload["request_id"] == "req-1"
    assert payload["run_id"] == "run-1"


def test_omits_uvicorn_color_message() -> None:
    payload = json.loads(JsonFormatter().format(make_record(color_message="\x1b[36mhi\x1b[0m")))

    assert "color_message" not in payload


def test_serializes_exception_and_non_json_values() -> None:
    try:
        raise ValueError("boom")
    except ValueError:
        record = make_record(obj=object())
        record.exc_info = sys.exc_info()

    payload = json.loads(JsonFormatter().format(record))

    assert "ValueError: boom" in payload["exc_info"]
    assert payload["obj"].startswith("<object object")
