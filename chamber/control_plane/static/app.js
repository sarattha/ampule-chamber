(() => {
  const wizard = document.querySelector("[data-wizard]");
  if (wizard) initializeWizard(wizard);
  const livePage = document.querySelector("[data-job-id]");
  if (livePage) initializeLivePage(livePage);

  function initializeWizard(form) {
    const panels = [...form.querySelectorAll("[data-step]")];
    const stepButtons = [...form.querySelectorAll("[data-step-button]")];
    const back = form.querySelector("[data-back]");
    const next = form.querySelector("[data-next]");
    const submit = form.querySelector("[data-submit]");
    const kubernetesFields = form.querySelector("[data-kubernetes-fields]");
    let current = 0;

    const render = () => {
      panels.forEach((panel, index) => panel.hidden = index !== current);
      stepButtons.forEach((button, index) => {
        button.classList.toggle("active", index === current);
        button.classList.toggle("done", index < current);
        button.setAttribute("aria-current", index === current ? "step" : "false");
      });
      back.disabled = current === 0;
      next.hidden = current === panels.length - 1;
      submit.hidden = current !== panels.length - 1;
      if (current === panels.length - 1) updateReview(form);
      panels[current].querySelector("h1")?.focus({preventScroll: true});
    };
    const valid = () => {
      const fields = [...panels[current].querySelectorAll("input,select")].filter(field => !field.closest("[hidden]"));
      return fields.every(field => field.reportValidity());
    };
    next.addEventListener("click", () => { if (valid() && current < panels.length - 1) { current += 1; render(); } });
    back.addEventListener("click", () => { if (current > 0) { current -= 1; render(); } });
    stepButtons.forEach((button, index) => button.addEventListener("click", () => { if (index <= current || valid()) { current = index; render(); } }));
    form.querySelectorAll('input[name="execution_mode"]').forEach(input => input.addEventListener("change", () => {
      const kubernetes = input.checked && input.value === "kubernetes";
      kubernetesFields.hidden = !kubernetes;
      form.querySelectorAll(".mode-card").forEach(card => card.classList.toggle("selected", card.querySelector("input").checked));
      const context = form.elements.kubernetes_context;
      context.required = kubernetes;
    }));
    form.querySelector("[data-advanced]").addEventListener("click", () => {
      updateReview(form);
      const values = Object.fromEntries(new FormData(form).entries());
      alert(Object.entries(values).filter(([key]) => key !== "_csrf").map(([key,value]) => `${key}: ${value}`).join("\n"));
    });
    render();
  }

  function updateReview(form) {
    const data = new FormData(form);
    form.querySelectorAll("[data-review]").forEach(node => {
      const key = node.dataset.review;
      const value = data.get(key);
      node.textContent = value || (key === "service_name" ? "Inferred" : key === "kubernetes_context" ? "Not applicable" : "—");
    });
  }

  function initializeLivePage(page) {
    const jobId = page.dataset.jobId;
    const source = new EventSource(`/api/v1/jobs/${jobId}/events`);
    source.addEventListener("job", event => {
      const job = JSON.parse(event.data);
      page.querySelector("[data-job-title]").textContent = title(job.state);
      page.querySelector("[data-job-state]").textContent = job.state;
      page.querySelector("[data-run-id]").textContent = job.run_id || "Allocating…";
      page.querySelector("[data-job-output]").textContent = job.output || (job.error ? job.error : "Assessment process is running…");
      const stages = [...page.querySelectorAll(".stage-list li")];
      const stageIndex = job.run_id ? 1 : 0;
      stages.forEach((stage, index) => stage.classList.toggle("active", index <= stageIndex));
      if (["completed", "failed", "cancelled"].includes(job.state)) {
        source.close();
        const button = page.querySelector("[data-cancel-form] button");
        button.disabled = true;
        if (job.run_id) window.setTimeout(() => window.location.assign(`/runs/${job.run_id}`), 900);
      }
    });
    source.onerror = () => { page.querySelector("[data-job-message]").textContent = "Connection interrupted. Chamber will reconnect automatically; the assessment continues server-side."; };
  }

  function title(value) { return value.replaceAll("_", " ").replace(/\b\w/g, letter => letter.toUpperCase()); }
})();
