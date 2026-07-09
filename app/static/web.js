(function () {
  const loginForm = document.querySelector("#login-form");
  if (loginForm) {
    wireLogin(loginForm);
  }

  const queryForm = document.querySelector("#query-form");
  if (queryForm) {
    wireQuery(queryForm);
  }

  const logoutButton = document.querySelector("#logout-button");
  if (logoutButton) {
    logoutButton.addEventListener("click", async () => {
      await fetch("/auth/logout", { method: "POST", credentials: "same-origin" });
      window.location.assign("/login");
    });
  }
})();

function wireLogin(form) {
  const error = document.querySelector("#login-error");
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    setFormBusy(form, true);
    showFormError(error, "");
    try {
      const response = await fetch("/auth/login", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          email: form.email.value,
          password: form.password.value,
        }),
      });
      if (!response.ok) {
        const body = await safeJson(response);
        throw new Error(apiErrorMessage(body, "Invalid email or password."));
      }
      window.location.assign("/app");
    } catch (errorValue) {
      showFormError(error, caughtErrorMessage(errorValue, "Sign in failed."));
    } finally {
      setFormBusy(form, false);
    }
  });
}

function wireQuery(form) {
  const resultRegion = document.querySelector("#result-region");
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    setFormBusy(form, true);
    setResult(resultRegion, loadingState());
    try {
      const response = await fetch("/query", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          question: form.question.value,
          limit: Number(form.limit.value || 5),
        }),
      });
      if (response.status === 401) {
        window.location.assign("/login");
        return;
      }
      const body = await safeJson(response);
      if (!response.ok) {
        throw new Error(apiErrorMessage(body, "Query failed."));
      }
      setResult(resultRegion, renderResponse(body));
    } catch (errorValue) {
      setResult(
        resultRegion,
        element("div", { className: "error-state" }, [
          caughtErrorMessage(errorValue, "Query failed."),
        ]),
      );
    } finally {
      setFormBusy(form, false);
    }
  });
}

function renderResponse(body) {
  const shell = element("div", { className: "result-shell" });
  const header = element("div", { className: "result-header" }, [
    element("span", { className: "route-badge", text: body.route || "result" }),
  ]);
  shell.append(header);

  if (body.message) {
    shell.append(element("p", { text: body.message }));
    return shell;
  }

  if (body.answer) {
    renderAnswer(shell, header, body.answer);
  }
  if (body.hpd_violations) {
    renderHpdViolations(shell, body.hpd_violations);
  }
  return shell;
}

function renderAnswer(shell, header, answer) {
  const statusClass =
    answer.answer_status === "unsupported" ? " status-unsupported" : "";
  header.append(
    element("span", {
      className: `status-badge${statusClass}`,
      text: answer.answer_status,
    }),
  );
  shell.append(element("p", { className: "answer-text", text: answer.answer }));

  if (answer.citations && answer.citations.length > 0) {
    shell.append(element("h2", { className: "section-title", text: "Citations" }));
    const list = element("ul", { className: "citation-list" });
    answer.citations.forEach((citation) => {
      const label = citation.citation || citation.source_name || "Source";
      const source = citation.source_url
        ? element("a", {
            href: citation.source_url,
            target: "_blank",
            rel: "noopener noreferrer",
            text: label,
          })
        : element("span", { text: label });
      list.append(
        element("li", { className: "citation-item" }, [
          source,
          element("span", {
            className: "citation-source",
            text: citation.source_name,
          }),
        ]),
      );
    });
    shell.append(list);
  }

  if (answer.source_coverage) {
    shell.append(
      element("p", {
        className: "meta-line",
        text: answer.source_coverage,
      }),
    );
  }
  if (answer.disclaimer) {
    shell.append(element("p", { className: "meta-line", text: answer.disclaimer }));
  }
}

function renderHpdViolations(shell, hpdViolations) {
  shell.append(
    element("h2", {
      className: "section-title",
      text: `HPD violations (${hpdViolations.count})`,
    }),
  );
  if (!hpdViolations.results || hpdViolations.results.length === 0) {
    shell.append(element("p", { text: "No HPD violations matched." }));
    return;
  }

  const table = element("table", { className: "hpd-table" });
  table.append(
    element("thead", {}, [
      element("tr", {}, [
        element("th", { text: "Inspection" }),
        element("th", { text: "Class" }),
        element("th", { text: "Status" }),
        element("th", { text: "Address" }),
        element("th", { text: "Description" }),
      ]),
    ]),
  );
  const body = element("tbody");
  hpdViolations.results.forEach((violation) => {
    body.append(
      element("tr", {}, [
        element("td", { text: violation.inspection_date || "" }),
        element("td", { text: violation.violation_class || "" }),
        element("td", { text: violation.current_status || "" }),
        element("td", { text: formatAddress(violation) }),
        element("td", { text: violation.nov_description || "" }),
      ]),
    );
  });
  table.append(body);
  shell.append(element("div", { className: "hpd-table-wrap" }, [table]));
}

function formatAddress(violation) {
  return [violation.house_number, violation.street_name, violation.zip_code]
    .filter(Boolean)
    .join(" ");
}

function loadingState() {
  return element("div", { className: "loading-state", text: "Loading..." });
}

function setResult(region, child) {
  region.className = "";
  region.replaceChildren(child);
}

function setFormBusy(form, busy) {
  Array.from(form.elements).forEach((control) => {
    control.disabled = busy;
  });
}

function showFormError(target, message) {
  if (!target) {
    return;
  }
  target.textContent = message;
  target.hidden = !message;
}

async function safeJson(response) {
  try {
    return await response.json();
  } catch (_error) {
    return {};
  }
}

function apiErrorMessage(body, fallback) {
  if (!body || body.detail === undefined) {
    return fallback;
  }
  return detailMessage(body.detail) || fallback;
}

function caughtErrorMessage(errorValue, fallback) {
  if (errorValue instanceof Error && errorValue.message) {
    return errorValue.message;
  }
  return detailMessage(errorValue) || fallback;
}

function detailMessage(detail) {
  if (typeof detail === "string") {
    return detail;
  }
  if (Array.isArray(detail)) {
    return detail.map(detailMessage).filter(Boolean).join(" ");
  }
  if (detail && typeof detail === "object") {
    if (typeof detail.msg === "string") {
      const location = Array.isArray(detail.loc) ? detail.loc.join(".") : "";
      return location ? `${location}: ${detail.msg}` : detail.msg;
    }
    try {
      return JSON.stringify(detail);
    } catch (_error) {
      return "";
    }
  }
  return "";
}

function element(tagName, attrs = {}, children = []) {
  const node = document.createElement(tagName);
  Object.entries(attrs).forEach(([key, value]) => {
    if (value === undefined || value === null) {
      return;
    }
    if (key === "text") {
      node.textContent = value;
    } else if (key === "className") {
      node.className = value;
    } else {
      node.setAttribute(key, value);
    }
  });
  children.forEach((child) => {
    node.append(child instanceof Node ? child : document.createTextNode(child));
  });
  return node;
}
