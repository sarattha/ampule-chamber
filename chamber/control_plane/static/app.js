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
    const repositoryTarget = form.querySelector("[data-repository-target]");
    const kubernetesTarget = form.querySelector("[data-kubernetes-target]");
    const serviceOptional = form.querySelector("[data-service-optional]");
    const repo = form.elements.repo;
    const serviceName = form.elements.service_name;
    const workloadName = form.elements.workload_name;
    const journeyList = form.querySelector("[data-journey-list]");
    const journeyTemplate = form.querySelector("[data-journey-template]");
    const journeysJson = form.querySelector("[data-journeys-json]");
    const discoveryPanel = form.querySelector("[data-discovery-panel]");
    const discoveryButton = form.querySelector("[data-discover-targets]");
    const discoveryStatus = form.querySelector("[data-discovery-status]");
    const discoveryResults = form.querySelector("[data-discovery-results]");
    const discoveredService = form.querySelector("[data-discovered-service]");
    const discoveredWorkload = form.querySelector("[data-discovered-workload]");
    let discoveryData = null;
    let current = 0;

    const profiles = {
      smoke: [{duration: "15s", targetVus: 1}, {duration: "5s", targetVus: 0}],
      baseline: [{duration: "30s", targetVus: 4}, {duration: "30s", targetVus: 0}],
      stress: [{duration: "30s", targetVus: 10}, {duration: "60s", targetVus: 25}, {duration: "30s", targetVus: 0}],
    };

    const field = (card, name) => card.querySelector(`[data-journey-field="${name}"]`);
    const multipartField = (row, name) => row.querySelector(`[data-multipart-file-field="${name}"]`);
    const parseJson = (control, label, {required = false} = {}) => {
      const raw = control.value.trim();
      control.setCustomValidity("");
      if (!raw && !required) return null;
      try { return JSON.parse(raw); }
      catch (_) {
        control.setCustomValidity(`${label} must be valid JSON.`);
        throw new Error(`${label} must be valid JSON.`);
      }
    };

    const renumberJourneys = () => {
      const cards = [...journeyList.querySelectorAll("[data-journey]")];
      cards.forEach((card, index) => {
        card.querySelector("[data-journey-number]").textContent = `Journey ${index + 1}`;
        card.querySelector("[data-remove-journey]").disabled = cards.length === 1;
      });
    };

    const syncMultipartFiles = (card, active) => {
      const rows = [...card.querySelectorAll("[data-multipart-file]")];
      rows.forEach(row => {
        const upload = multipartField(row, "file");
        upload.disabled = !active;
        upload.required = active && multipartField(row, "required").checked;
        row.querySelector("[data-remove-multipart-file]").disabled = rows.length === 1;
      });
    };

    const addMultipartFile = (card, {fieldName} = {}) => {
      const template = card.querySelector("[data-multipart-file-template]");
      const fragment = template.content.cloneNode(true);
      const row = fragment.querySelector("[data-multipart-file]");
      const count = card.querySelectorAll("[data-multipart-file]").length + 1;
      multipartField(row, "field").value = fieldName || (count === 1 ? "file" : `file_${count}`);
      row.querySelector("[data-remove-multipart-file]").addEventListener("click", () => {
        row.remove();
        syncMultipartFiles(card, field(card, "requestEncoding").value === "multipart");
      });
      card.querySelector("[data-multipart-file-list]").appendChild(fragment);
      syncMultipartFiles(card, field(card, "requestEncoding").value === "multipart");
      return row;
    };

    const syncJourney = (card, {resetDefaults = false} = {}) => {
      const adapter = field(card, "adapter").value;
      const relayna = adapter === "relayna";
      const loadModel = field(card, "loadModel").value;
      const requestEncoding = field(card, "requestEncoding").value;
      card.querySelector("[data-relayna-settings]").hidden = !relayna;
      card.querySelector("[data-load-profile]").hidden = loadModel !== "profile" || relayna;
      card.querySelector("[data-custom-stages]").hidden = loadModel !== "stages" || relayna;
      card.querySelector("[data-fixed-iterations]").hidden = loadModel !== "iterations" && !relayna;
      card.querySelector("[data-json-request]").hidden = requestEncoding !== "json";
      card.querySelector("[data-multipart-request]").hidden = requestEncoding !== "multipart";
      card.querySelector("[data-form-request]").hidden = requestEncoding !== "form";
      card.querySelector("[data-raw-request]").hidden = requestEncoding !== "raw";
      card.querySelector("[data-text-bytes]").hidden = requestEncoding !== "json";
      [...field(card, "requestEncoding").options].forEach(option => {
        option.disabled = relayna && !["json", "multipart"].includes(option.value);
      });
      field(card, "body").required = relayna && requestEncoding === "json";
      field(card, "multipartFields").required = false;
      field(card, "form").required = requestEncoding === "form";
      field(card, "rawBody").required = requestEncoding === "raw";
      field(card, "contentType").required = requestEncoding === "raw";
      field(card, "eventsPath").required = relayna;
      field(card, "taskIdPath").required = relayna;
      syncMultipartFiles(card, requestEncoding === "multipart");
      if (resetDefaults) {
        field(card, "method").value = relayna ? "POST" : "GET";
        field(card, "path").value = relayna ? "/translations" : "/health";
        field(card, "expectedStatus").value = relayna ? "202" : "200";
        field(card, "loadModel").value = relayna ? "iterations" : "profile";
        field(card, "requestEncoding").value = relayna ? "json" : "none";
        if (relayna && !field(card, "body").value.trim()) {
          field(card, "body").value = '{"text":"Hello from Ampule Chamber.","language_target":"Thai","priority":5}';
        }
        syncJourney(card);
      }
      card.querySelector("[data-journey-title]").textContent = `${relayna ? "Relayna" : "HTTP"} · ${field(card, "name").value || "unnamed"}`;
    };

    const addJourney = ({adapter = "http"} = {}) => {
      const fragment = journeyTemplate.content.cloneNode(true);
      const card = fragment.querySelector("[data-journey]");
      const count = journeyList.querySelectorAll("[data-journey]").length + 1;
      field(card, "name").value = count === 1 ? "baseline-health" : `traffic-${count}`;
      field(card, "adapter").value = adapter;
      card.addEventListener("input", () => syncJourney(card));
      field(card, "adapter").addEventListener("change", () => syncJourney(card, {resetDefaults: true}));
      field(card, "loadModel").addEventListener("change", () => syncJourney(card));
      field(card, "requestEncoding").addEventListener("change", () => syncJourney(card));
      card.querySelector("[data-add-multipart-file]").addEventListener("click", () => addMultipartFile(card));
      card.querySelector("[data-remove-journey]").addEventListener("click", () => {
        card.remove();
        renumberJourneys();
      });
      addMultipartFile(card);
      journeyList.appendChild(fragment);
      syncJourney(card, {resetDefaults: adapter === "relayna"});
      renumberJourneys();
      return card;
    };

    const serializeJourneys = ({prepareUploads = false} = {}) => {
      const cards = [...journeyList.querySelectorAll("[data-journey]")];
      if (!cards.length) throw new Error("Add at least one traffic journey.");
      const adapters = new Set(cards.map(card => field(card, "adapter").value));
      if (adapters.size > 1) throw new Error("One assessment cannot mix HTTP and Relayna journeys.");
      let uploadIndex = 0;
      const journeys = cards.map((card, index) => {
        const adapter = field(card, "adapter").value;
        const loadModel = field(card, "loadModel").value;
        const requestEncoding = field(card, "requestEncoding").value;
        const journey = {
          name: field(card, "name").value.trim(),
          method: field(card, "method").value,
          path: field(card, "path").value.trim(),
          expectedStatus: Number(field(card, "expectedStatus").value),
          requestEncoding,
        };
        const tool = field(card, "tool").value.trim();
        if (tool) journey.tool = tool;
        if (adapter === "relayna") journey.adapter = "relayna";
        if (requestEncoding === "json") {
          const body = parseJson(field(card, "body"), `Journey ${index + 1} request body`, {required: adapter === "relayna"});
          if (body !== null) journey.body = body;
          const textBytes = Number(field(card, "textBytes").value);
          if (textBytes > 0) journey.textBytes = textBytes;
        } else if (requestEncoding === "multipart") {
          const fields = parseJson(field(card, "multipartFields"), `Journey ${index + 1} multipart fields`) || {};
          if (Array.isArray(fields) || typeof fields !== "object") {
            field(card, "multipartFields").setCustomValidity("Multipart fields must be a JSON object.");
            throw new Error("Multipart fields must be a JSON object.");
          }
          const fileRows = [...card.querySelectorAll("[data-multipart-file]")];
          if (!fileRows.length) throw new Error(`Journey ${index + 1} requires at least one file row.`);
          const files = fileRows.map((row, fileIndex) => {
            const uploadControl = multipartField(row, "file");
            const upload = uploadControl.files[0];
            const required = multipartField(row, "required").checked;
            const fileField = multipartField(row, "field").value.trim();
            if (!fileField) throw new Error(`Journey ${index + 1} file ${fileIndex + 1} requires a field name.`);
            if (required && !upload) throw new Error(`Journey ${index + 1} required file ${fileField} needs an upload.`);
            uploadControl.disabled = prepareUploads && !upload;
            const item = {field: fileField, required};
            if (upload) {
              const filename = multipartField(row, "filename").value.trim() || upload.name;
              const contentType = multipartField(row, "contentType").value.trim() || upload.type;
              if (!contentType) throw new Error(`Journey ${index + 1} file ${fileField} requires a content type.`);
              Object.assign(item, {filename, contentType, uploadIndex});
              uploadIndex += 1;
            }
            return item;
          });
          journey.multipart = {
            fields,
            files,
          };
        } else if (requestEncoding === "form") {
          const formFields = parseJson(field(card, "form"), `Journey ${index + 1} form fields`, {required: true});
          if (!formFields || Array.isArray(formFields) || typeof formFields !== "object") {
            field(card, "form").setCustomValidity("Form fields must be a JSON object.");
            throw new Error("Form fields must be a JSON object.");
          }
          journey.form = formFields;
        } else if (requestEncoding === "raw") {
          journey.body = field(card, "rawBody").value;
          journey.contentType = field(card, "contentType").value.trim();
        }
        if (adapter === "relayna" || loadModel === "iterations") {
          journey.vus = Number(field(card, "vus").value);
          journey.iterations = Number(field(card, "iterations").value);
          journey.durationSeconds = Number(field(card, "durationSeconds").value);
        } else if (loadModel === "stages") {
          const stages = parseJson(field(card, "stages"), `Journey ${index + 1} stages`, {required: true});
          if (!Array.isArray(stages) || !stages.length) {
            field(card, "stages").setCustomValidity("Stages must be a non-empty JSON array.");
            throw new Error("Stages must be a non-empty JSON array.");
          }
          journey.stages = stages;
        } else {
          journey.stages = profiles[field(card, "profile").value];
        }
        if (adapter === "relayna") {
          journey.relayna = {
            taskIdPath: field(card, "taskIdPath").value.trim(),
            eventsPath: field(card, "eventsPath").value.trim(),
            terminalStatuses: field(card, "terminalStatuses").value.split(",").map(value => value.trim()).filter(Boolean),
            successStatuses: field(card, "successStatuses").value.split(",").map(value => value.trim()).filter(Boolean),
            timeoutSeconds: Number(field(card, "timeoutSeconds").value),
          };
        }
        const followUps = parseJson(field(card, "followUps"), `Journey ${index + 1} follow-up checks`);
        if (followUps !== null) journey.followUps = followUps;
        return journey;
      });
      journeysJson.value = JSON.stringify(journeys);
      return journeys;
    };

    const selectModeCard = input => {
      input.closest(".mode-grid").querySelectorAll(".mode-card").forEach(card =>
        card.classList.toggle("selected", card.querySelector("input").checked));
    };

    const selectAttachedTarget = attached => {
      repositoryTarget.hidden = attached;
      kubernetesTarget.hidden = !attached;
      serviceOptional.hidden = attached;
      repo.required = !attached;
      serviceName.required = false;
      workloadName.required = false;
      if (!attached) return;
      const kubernetes = form.querySelector('input[name="execution_mode"][value="kubernetes"]');
      kubernetes.checked = true;
      selectModeCard(kubernetes);
      kubernetesFields.hidden = false;
      form.elements.kubernetes_context.required = true;
      form.elements.namespace.required = true;
      form.elements.runtime_mode.value = "attach";
      updateDiscoveryVisibility();
    };

    const updateDiscoveryVisibility = () => {
      const attachedTarget = form.querySelector('input[name="target_source"][value="kubernetes"]').checked;
      const kubernetesMode = form.querySelector('input[name="execution_mode"][value="kubernetes"]').checked;
      discoveryPanel.hidden = !(attachedTarget && kubernetesMode && form.elements.runtime_mode.value === "attach");
    };

    const applyWorkload = () => {
      const workload = (discoveryData?.workloads || []).find(item => `${item.kind}/${item.name}` === discoveredWorkload.value);
      if (!workload) return;
      workloadName.value = workload.name;
      form.elements.workload_kind.value = workload.kind;
    };

    const applyService = () => {
      const service = (discoveryData?.services || []).find(item => item.name === discoveredService.value);
      if (!service) return;
      serviceName.value = service.name;
      if (service.ports.length) form.elements.service_port.value = service.ports[0].port;
      const candidates = service.workloads.length ? service.workloads : discoveryData.workloads;
      const needsExplicitChoice = service.workloads.length !== 1;
      const options = candidates.map(item =>
        new Option(`${item.name} · ${item.kind}`, `${item.kind}/${item.name}`));
      if (needsExplicitChoice) {
        options.unshift(new Option("Select the backing workload…", "", true, true));
        workloadName.value = "";
      }
      discoveredWorkload.replaceChildren(...options);
      discoveredWorkload.required = candidates.length > 0;
      if (!needsExplicitChoice) applyWorkload();
      if (needsExplicitChoice && candidates.length) {
        discoveryStatus.textContent = "No unique workload match was found. Choose the backing workload explicitly.";
      } else if (!candidates.length) {
        discoveryStatus.textContent = "No workload was found. Enter its name and kind in the Target step.";
      } else {
        discoveryStatus.textContent = `Matched ${service.name} to ${candidates[0].kind} ${candidates[0].name}.`;
      }
    };

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
      const fields = [...panels[current].querySelectorAll("input,select,textarea")].filter(field => !field.closest("[hidden]"));
      if (!fields.every(field => field.reportValidity())) return false;
      if (current === 2) {
        try { serializeJourneys(); }
        catch (error) {
          const invalid = panels[current].querySelector(":invalid");
          if (invalid) invalid.reportValidity(); else window.alert(error.message);
          return false;
        }
      }
      return true;
    };
    next.addEventListener("click", () => { if (valid() && current < panels.length - 1) { current += 1; render(); } });
    back.addEventListener("click", () => { if (current > 0) { current -= 1; render(); } });
    stepButtons.forEach((button, index) => button.addEventListener("click", () => { if (index <= current || valid()) { current = index; render(); } }));
    form.querySelectorAll('input[name="execution_mode"]').forEach(input => input.addEventListener("change", () => {
      const kubernetes = input.checked && input.value === "kubernetes";
      kubernetesFields.hidden = !kubernetes;
      selectModeCard(input);
      const context = form.elements.kubernetes_context;
      context.required = kubernetes;
      updateDiscoveryVisibility();
    }));
    form.querySelectorAll('input[name="target_source"]').forEach(input => input.addEventListener("change", () => {
      selectModeCard(input);
      selectAttachedTarget(input.checked && input.value === "kubernetes");
    }));
    form.elements.runtime_mode.addEventListener("change", updateDiscoveryVisibility);
    discoveredService.addEventListener("change", applyService);
    discoveredWorkload.addEventListener("change", applyWorkload);
    discoveryButton.addEventListener("click", async () => {
      const context = form.elements.kubernetes_context.value.trim();
      const namespace = form.elements.namespace.value.trim();
      if (!context || !namespace) {
        discoveryStatus.textContent = "Enter the Kubernetes context and namespace first.";
        discoveryStatus.classList.add("error");
        return;
      }
      discoveryButton.disabled = true;
      discoveryStatus.classList.remove("error");
      discoveryStatus.textContent = "Reading namespace inventory…";
      try {
        const query = new URLSearchParams({context, namespace});
        const response = await fetch(`/api/v1/kubernetes/discovery?${query}`);
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.detail || "Discovery failed");
        discoveryData = payload;
        discoveredService.replaceChildren(...payload.services.map(service => {
          const ports = service.ports.map(port => port.port).join(", ") || "no ports";
          return new Option(`${service.name} · ${ports}`, service.name);
        }));
        discoveryResults.hidden = !payload.services.length;
        if (!payload.services.length) throw new Error("No Services were found in this namespace");
        applyService();
      } catch (error) {
        discoveryData = null;
        discoveryResults.hidden = true;
        discoveryStatus.textContent = error.message;
        discoveryStatus.classList.add("error");
      } finally {
        discoveryButton.disabled = false;
      }
    });
    form.querySelector("[data-add-journey]").addEventListener("click", () => {
      const first = journeyList.querySelector("[data-journey]");
      addJourney({adapter: first ? field(first, "adapter").value : "http"});
    });
    form.addEventListener("submit", event => {
      try { serializeJourneys({prepareUploads: true}); }
      catch (error) {
        event.preventDefault();
        journeyList.querySelectorAll("[data-journey]").forEach(card => syncJourney(card));
        window.alert(error.message);
      }
    });
    form.querySelector("[data-advanced]").addEventListener("click", () => {
      updateReview(form);
      const values = Object.fromEntries(new FormData(form).entries());
      alert(Object.entries(values).filter(([key]) => key !== "_csrf").map(([key,value]) => `${key}: ${value}`).join("\n"));
    });
    addJourney();
    render();
  }

  function updateReview(form) {
    const data = new FormData(form);
    form.querySelectorAll("[data-review]").forEach(node => {
      const key = node.dataset.review;
      const value = data.get(key);
      const fallback = key === "service_name" ? "Inferred"
        : key === "kubernetes_context" ? "Not applicable"
        : key === "repo" && data.get("target_source") === "kubernetes" ? "Not required"
        : "—";
      node.textContent = value || fallback;
    });
    const journeys = form.querySelectorAll("[data-journey]");
    const adapters = new Set([...journeys].map(card => card.querySelector('[data-journey-field="adapter"]').value));
    const summary = form.querySelector("[data-review-journeys]");
    if (summary) summary.textContent = `${journeys.length} ${[...adapters].join(" + ").toUpperCase()} journey${journeys.length === 1 ? "" : "s"}`;
    const selectedFiles = [...form.querySelectorAll('[data-multipart-file-field="file"]')]
      .flatMap(control => [...control.files]);
    const fileSummary = form.querySelector("[data-review-files]");
    if (fileSummary) {
      fileSummary.textContent = selectedFiles.length
        ? `${selectedFiles.length} file${selectedFiles.length === 1 ? "" : "s"}: ${selectedFiles.map(file => `${file.name} (${file.type || "unknown type"}, ${file.size} bytes)`).join(", ")}`
        : "No multipart uploads";
    }
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
