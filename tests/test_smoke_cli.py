from app.cli.smoke import run_mvp_eval


def test_mvp_eval_accepts_expected_responses():
    failures: list[str] = []

    run_mvp_eval(ExpectedEvalClient(), failures)

    assert failures == []


def test_mvp_eval_records_actionable_failures():
    failures: list[str] = []

    run_mvp_eval(FailingEvalClient(), failures)

    assert "MVP eval RPAPL cites RPAPL § 711" in failures
    assert "MVP eval HPD returns property matches" in failures


class FakeResponse:
    def __init__(self, status_code: int, body: dict):
        self.status_code = status_code
        self._body = body

    def json(self):
        return self._body


class ExpectedEvalClient:
    def post(self, path: str, json: dict):
        question = json["question"]
        if "RPAPL" in question:
            return FakeResponse(
                200,
                {
                    "route": "legal",
                    "answer": {
                        "answer_status": "answered",
                        "citations": [{"citation": "RPAPL § 711"}],
                    },
                },
            )
        if "MDL" in question:
            return FakeResponse(
                200,
                {
                    "route": "legal",
                    "answer": {
                        "answer_status": "answered",
                        "citations": [{"citation": "Multiple Dwelling Law § 78"}],
                    },
                },
            )
        if "22 FRONT" in question:
            return FakeResponse(
                200,
                {"route": "property", "hpd_violations": {"count": 37}},
            )
        if "NO SUCH STREET" in question:
            return FakeResponse(
                200,
                {"route": "property", "hpd_violations": {"count": 0}},
            )
        return FakeResponse(
            200,
            {"route": "legal", "answer": {"answer_status": "unsupported"}},
        )


class FailingEvalClient(ExpectedEvalClient):
    def post(self, path: str, json: dict):
        question = json["question"]
        if "RPAPL" in question:
            return FakeResponse(
                200,
                {
                    "route": "legal",
                    "answer": {
                        "answer_status": "answered",
                        "citations": [{"citation": "HPD Guidance"}],
                    },
                },
            )
        if "22 FRONT" in question:
            return FakeResponse(
                200,
                {"route": "property", "hpd_violations": {"count": 0}},
            )
        return super().post(path, json)
