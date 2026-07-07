import argparse
import sys

import httpx


def run_smoke_test(
    base_url: str,
    email: str | None,
    password: str | None,
    mvp_eval: bool = False,
) -> int:
    base_url = base_url.rstrip("/")
    failures: list[str] = []
    with httpx.Client(base_url=base_url, timeout=15, follow_redirects=True) as client:
        health = client.get("/health")
        check(failures, health.status_code == 200, "GET /health")

        anonymous_search = client.post("/search", json={"query": "good repair"})
        check(
            failures,
            anonymous_search.status_code == 401,
            "anonymous POST /search returns 401",
        )

        anonymous_answer = client.post("/answer", json={"question": "good repair"})
        check(
            failures,
            anonymous_answer.status_code == 401,
            "anonymous POST /answer returns 401",
        )

        if email and password:
            login = client.post(
                "/auth/login",
                json={"email": email, "password": password},
            )
            check(failures, login.status_code == 200, "POST /auth/login")

            me = client.get("/auth/me")
            check(failures, me.status_code == 200, "GET /auth/me")

            search = client.post("/search", json={"query": "good repair", "limit": 3})
            check(failures, search.status_code == 200, "authenticated POST /search")

            answer = client.post(
                "/answer",
                json={"question": "What does the owner need to repair?", "limit": 3},
            )
            check(failures, answer.status_code == 200, "authenticated POST /answer")
            if mvp_eval:
                run_mvp_eval(client, failures)

    if failures:
        for failure in failures:
            print(f"FAIL: {failure}", file=sys.stderr)
        return 1
    print("Smoke test passed.")
    return 0


def check(failures: list[str], condition: bool, label: str) -> None:
    if not condition:
        failures.append(label)


def run_mvp_eval(client, failures: list[str]) -> None:
    rpapl = client.post(
        "/query",
        json={"question": "What does RPAPL section 711 cover?", "limit": 5},
    )
    check(failures, rpapl.status_code == 200, "MVP eval RPAPL query returns 200")
    if rpapl.status_code == 200:
        body = rpapl.json()
        check(failures, body.get("route") == "legal", "MVP eval RPAPL routes legal")
        answer = body.get("answer") or {}
        citations = answer.get("citations") or []
        check(
            failures,
            answer.get("answer_status") == "answered",
            "MVP eval RPAPL answered",
        )
        check(
            failures,
            bool(citations) and citations[0].get("citation") == "RPAPL § 711",
            "MVP eval RPAPL cites RPAPL § 711",
        )

    mdl = client.post(
        "/query",
        json={"question": "What does MDL section 78 cover?", "limit": 5},
    )
    check(failures, mdl.status_code == 200, "MVP eval MDL query returns 200")
    if mdl.status_code == 200:
        body = mdl.json()
        answer = body.get("answer") or {}
        citations = answer.get("citations") or []
        check(
            failures,
            body.get("route") == "legal",
            "MVP eval MDL routes legal",
        )
        check(
            failures,
            bool(citations)
            and citations[0].get("citation") == "Multiple Dwelling Law § 78",
            "MVP eval MDL cites Multiple Dwelling Law § 78",
        )

    hpd = client.post(
        "/query",
        json={"question": "Show HPD violations at 22 FRONT STAGG STREET", "limit": 5},
    )
    check(failures, hpd.status_code == 200, "MVP eval HPD query returns 200")
    if hpd.status_code == 200:
        body = hpd.json()
        violations = body.get("hpd_violations") or {}
        check(
            failures,
            body.get("route") == "property",
            "MVP eval HPD routes property",
        )
        check(
            failures,
            violations.get("count", 0) > 0,
            "MVP eval HPD returns property matches",
        )

    no_result = client.post(
        "/query",
        json={"question": "Show HPD violations at 999999 NO SUCH STREET", "limit": 5},
    )
    check(
        failures,
        no_result.status_code == 200,
        "MVP eval no-result property query returns 200",
    )
    if no_result.status_code == 200:
        body = no_result.json()
        violations = body.get("hpd_violations") or {}
        check(
            failures,
            body.get("route") == "property" and violations.get("count") == 0,
            "MVP eval no-result property query returns zero matches",
        )

    hmc = client.post(
        "/query",
        json={
            "question": "What does NYC Admin Code section 27-2005 require?",
            "limit": 5,
        },
    )
    check(failures, hmc.status_code == 200, "MVP eval HMC query returns 200")
    if hmc.status_code == 200:
        body = hmc.json()
        answer = body.get("answer") or {}
        check(
            failures,
            body.get("route") == "legal",
            "MVP eval HMC routes legal",
        )
        check(
            failures,
            answer.get("answer_status") in {"answered", "unsupported"},
            "MVP eval HMC returns a valid answer status",
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run API smoke tests.")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--email")
    parser.add_argument("--password")
    parser.add_argument(
        "--mvp-eval",
        action="store_true",
        help="Run deterministic MVP query checks after authenticated smoke checks.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if bool(args.email) != bool(args.password):
        print("--email and --password must be provided together.", file=sys.stderr)
        return 2
    if args.mvp_eval and not (args.email and args.password):
        print("--mvp-eval requires --email and --password.", file=sys.stderr)
        return 2
    return run_smoke_test(args.base_url, args.email, args.password, args.mvp_eval)


if __name__ == "__main__":
    raise SystemExit(main())
