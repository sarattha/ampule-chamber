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
    const scenarioStatus = form.querySelector("[data-scenario-status]");
    const savedScenarioPanel = form.querySelector("[data-saved-scenario]");
    const importScenarioPanel = form.querySelector("[data-import-scenario]");
    const scenarioSelect = form.querySelector("[data-scenario-select]");
    const scenarioMetadata = form.querySelector("[data-scenario-metadata]");
    const csrfToken = form.elements._csrf.value;
    let discoveryData = null;
    let scenarioCatalog = [];
    let scenarioWarnings = [];
    let current = 0;

    const profiles = {
      smoke: [{duration: "15s", targetVus: 1}, {duration: "5s", targetVus: 0}],
      baseline: [{duration: "30s", targetVus: 4}, {duration: "30s", targetVus: 0}],
      stress: [{duration: "30s", targetVus: 10}, {duration: "60s", targetVus: 25}, {duration: "30s", targetVus: 0}],
    };

    const field = (card, name) => card.querySelector(`[data-journey-field="${name}"]`);
    const existingMultipartFiles = card => {
      try {
        const files = JSON.parse(card.dataset.multipartFiles || "[]");
        return Array.isArray(files) ? files : [];
      } catch (_) { return []; }
    };
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
      field(card, "requestEncoding").disabled = relayna;
      field(card, "body").required = relayna;
      field(card, "file").disabled = requestEncoding !== "multipart";
      const retainedFiles = existingMultipartFiles(card);
      field(card, "file").required = requestEncoding === "multipart" && !retainedFiles.length;
      const existingFileStatus = card.querySelector("[data-existing-file]");
      existingFileStatus.hidden = requestEncoding !== "multipart" || !retainedFiles.length;
      existingFileStatus.textContent = retainedFiles.length
        ? `Using validated file: ${retainedFiles.map(item => item.filename || item.path).join(", ")}. Select a new upload to replace it.`
        : "";
      field(card, "multipartFields").required = requestEncoding === "multipart";
      field(card, "form").required = requestEncoding === "form";
      field(card, "rawBody").required = requestEncoding === "raw";
      field(card, "contentType").required = requestEncoding === "raw";
      field(card, "eventsPath").required = relayna;
      field(card, "taskIdPath").required = relayna;
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

    const addJourney = ({adapter = "http", journey = null} = {}) => {
      const fragment = journeyTemplate.content.cloneNode(true);
      const card = fragment.querySelector("[data-journey]");
      const count = journeyList.querySelectorAll("[data-journey]").length + 1;
      field(card, "name").value = count === 1 ? "baseline-health" : `traffic-${count}`;
      field(card, "adapter").value = adapter;
      card.addEventListener("input", () => syncJourney(card));
      field(card, "adapter").addEventListener("change", () => syncJourney(card, {resetDefaults: true}));
      field(card, "loadModel").addEventListener("change", () => syncJourney(card));
      field(card, "requestEncoding").addEventListener("change", () => syncJourney(card));
      card.querySelector("[data-remove-journey]").addEventListener("click", () => {
        card.remove();
        renumberJourneys();
      });
      journeyList.appendChild(fragment);
      syncJourney(card, {resetDefaults: adapter === "relayna"});
      if (journey) populateJourney(card, journey);
      renumberJourneys();
      return card;
    };

    const populateJourney = (card, journey) => {
      const adapter = journey.adapter || "http";
      field(card, "adapter").value = adapter;
      field(card, "name").value = journey.name || "traffic";
      field(card, "method").value = (journey.method || "GET").toUpperCase();
      field(card, "path").value = journey.path || "/health";
      field(card, "expectedStatus").value = journey.expectedStatus || 200;
      field(card, "tool").value = journey.tool || "k6";
      const encoding = journey.requestEncoding || (Object.hasOwn(journey, "body") ? "json" : "none");
      field(card, "requestEncoding").value = encoding;
      if (encoding === "json") field(card, "body").value = JSON.stringify(journey.body ?? {}, null, 2);
      if (encoding === "multipart") {
        const files = Array.isArray(journey.multipart?.files) ? journey.multipart.files : [];
        const retainedFiles = files.filter(item => item?.path && item?.pathToken);
        card.dataset.multipartFiles = JSON.stringify(retainedFiles);
        field(card, "multipartFields").value = JSON.stringify(journey.multipart?.fields || {}, null, 2);
        field(card, "fileField").value = files[0]?.field || "file";
      }
      if (encoding === "form") field(card, "form").value = JSON.stringify(journey.form || {}, null, 2);
      if (encoding === "raw") {
        field(card, "rawBody").value = journey.body || "";
        field(card, "contentType").value = journey.contentType || "text/plain";
      }
      field(card, "textBytes").value = journey.textBytes || 0;
      if (Array.isArray(journey.stages)) {
        field(card, "loadModel").value = "stages";
        field(card, "stages").value = JSON.stringify(journey.stages, null, 2);
      } else {
        field(card, "loadModel").value = "iterations";
        field(card, "vus").value = journey.vus || 1;
        field(card, "iterations").value = journey.iterations || 1;
        field(card, "durationSeconds").value = journey.durationSeconds || 1;
      }
      if (adapter === "relayna") {
        const lifecycle = journey.relayna || {};
        field(card, "taskIdPath").value = lifecycle.taskIdPath || "task_id";
        field(card, "eventsPath").value = lifecycle.eventsPath || "/events/{task_id}";
        field(card, "terminalStatuses").value = (lifecycle.terminalStatuses || ["completed", "failed"]).join(", ");
        field(card, "successStatuses").value = (lifecycle.successStatuses || ["completed"]).join(", ");
        field(card, "timeoutSeconds").value = lifecycle.timeoutSeconds || 300;
      }
      field(card, "followUps").value = journey.followUps ? JSON.stringify(journey.followUps, null, 2) : "";
      syncJourney(card);
    };

    const selectedServiceName = () => serviceName.value.trim();
    const setScenarioStatus = (message, tone = "") => {
      scenarioStatus.textContent = message;
      scenarioStatus.classList.toggle("error", tone === "error");
      scenarioStatus.classList.toggle("warning", tone === "warning");
    };
    const applyScenario = projection => {
      const identity = projection.identity;
      form.elements.scenario_id.value = identity.id;
      form.elements.scenario_name.value = identity.name;
      form.elements.scenario_description.value = identity.description || "";
      form.elements.scenario_tags.value = (identity.tags || []).join(", ");
      form.elements.scenario_source.value = projection.source;
      form.elements.scenario_revision.value = projection.revision;
      form.elements.required_signals_json.value = JSON.stringify(projection.requiredSignals || []);
      form.elements.agents_mode.value = projection.agentMode || "offline";
      form.elements.fault_type.value = "none";
      journeyList.replaceChildren();
      projection.journeys.forEach(journey => addJourney({adapter: journey.adapter || "http", journey}));
      scenarioWarnings = projection.warnings || [];
      form.dataset.scenarioWarnings = JSON.stringify(scenarioWarnings);
      const faultMessage = projection.recommendedFault && projection.recommendedFault !== "none"
        ? ` ${projection.recommendedFault} is recommended but remains disabled; choose it explicitly below.` : "";
      const warningMessage = scenarioWarnings.length ? ` ${scenarioWarnings.join(" ")}` : "";
      setScenarioStatus(`Loaded ${identity.id} into the editable exercise.${faultMessage}${warningMessage}`, faultMessage || warningMessage ? "warning" : "");
    };

    const filteredScenarios = () => {
      const query = form.querySelector("[data-scenario-search]").value.trim().toLowerCase();
      const adapter = form.querySelector("[data-scenario-adapter]").value;
      const fault = form.querySelector("[data-scenario-fault]").value;
      return scenarioCatalog.filter(item => {
        const searchable = [item.id, item.name, item.description, ...(item.tags || [])].join(" ").toLowerCase();
        const adapterMatch = !adapter || item.trafficAdapters.includes(adapter);
        const hasFault = (item.faults || []).length > 0;
        const faultMatch = !fault || (fault === "configured" ? hasFault : !hasFault);
        return (!query || searchable.includes(query)) && adapterMatch && faultMatch;
      });
    };
    const renderScenarioCatalog = () => {
      const selected = scenarioSelect.value;
      const options = filteredScenarios().map(item => new Option(`${item.source === "bundled" ? "Bundled" : "Workspace"} · ${item.name} (${item.id})`, `${item.source}/${item.id}`));
      options.unshift(new Option(options.length ? "Select a saved scenario…" : "No matching scenarios", ""));
      scenarioSelect.replaceChildren(...options);
      if ([...scenarioSelect.options].some(option => option.value === selected)) scenarioSelect.value = selected;
      renderScenarioMetadata();
    };
    const selectedScenarioMetadata = () => scenarioCatalog.find(item => `${item.source}/${item.id}` === scenarioSelect.value);
    const renderScenarioMetadata = () => {
      const item = selectedScenarioMetadata();
      if (!item) {
        scenarioMetadata.textContent = "Select a scenario to inspect its metadata.";
        return;
      }
      scenarioMetadata.textContent = `${item.description || "No description"}\n${item.journeyCount} journey(s) · ${item.trafficAdapters.join(", ")} · max ${item.maxVirtualUsers} VUs · ${item.expectedDuration}\nFaults: ${(item.faults || []).join(", ") || "none"} · Signals: ${(item.requiredSignals || []).join(", ") || "default"}\nTags: ${(item.tags || []).join(", ") || "none"} · revision ${item.revision}`;
    };
    const loadScenarioCatalog = async () => {
      if (scenarioCatalog.length) return;
      try {
        const response = await fetch("/api/v1/scenarios");
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.detail || "Could not load scenario catalog");
        scenarioCatalog = payload.scenarios;
        renderScenarioCatalog();
      } catch (error) { setScenarioStatus(error.message, "error"); }
    };
    const validateImportedScenario = async content => {
      const response = await fetch("/api/v1/scenarios/validate", {
        method: "POST",
        headers: {"Content-Type": "application/json", "X-CSRF-Token": csrfToken},
        body: JSON.stringify({content, service_name: selectedServiceName()}),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || "Scenario validation failed");
      applyScenario(payload);
    };

    const serializeJourneys = () => {
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
          const fields = parseJson(field(card, "multipartFields"), `Journey ${index + 1} multipart fields`, {required: true});
          if (!fields || Array.isArray(fields) || typeof fields !== "object") {
            field(card, "multipartFields").setCustomValidity("Multipart fields must be a JSON object.");
            throw new Error("Multipart fields must be a JSON object.");
          }
          const upload = field(card, "file").files[0];
          const existingFiles = existingMultipartFiles(card);
          if (!upload && !existingFiles.length) {
            throw new Error(`Journey ${index + 1} requires an uploaded file.`);
          }
          let files;
          if (upload) {
            files = [{field: field(card, "fileField").value.trim(), uploadIndex}];
            uploadIndex += 1;
          } else {
            files = existingFiles.map((item, fileIndex) => {
              const retained = {
                field: fileIndex === 0 ? field(card, "fileField").value.trim() : item.field,
                path: item.path,
                pathToken: item.pathToken,
              };
              if (item.filename) retained.filename = item.filename;
              if (item.contentType) retained.contentType = item.contentType;
              return retained;
            });
          }
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
      if (current === 2 && !form.elements.scenario_id.value.trim()) {
        const base = (serviceName.value.trim() || repo.value.split("/").filter(Boolean).at(-1) || "service")
          .toLowerCase().replaceAll("_", "-").replace(/[^a-z0-9.-]+/g, "-").replace(/^-+|-+$/g, "");
        form.elements.scenario_id.value = `${base || "service"}-assessment`.slice(0, 63).replace(/[-.]$/, "");
        form.elements.scenario_name.value = `${serviceName.value.trim() || "Service"} reliability assessment`;
      }
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
    form.querySelectorAll('input[name="scenario_source_mode"]').forEach(input => input.addEventListener("change", () => {
      selectModeCard(input);
      savedScenarioPanel.hidden = input.value !== "saved";
      importScenarioPanel.hidden = input.value !== "imported";
      if (input.value === "saved") loadScenarioCatalog();
      if (input.value === "custom") {
        form.elements.scenario_source.value = "custom";
        form.elements.scenario_revision.value = "";
        scenarioWarnings = [];
        form.dataset.scenarioWarnings = "[]";
        setScenarioStatus("Custom exercise selected. Faults remain disabled unless you choose one below.");
      }
    }));
    form.querySelectorAll("[data-scenario-search],[data-scenario-adapter],[data-scenario-fault]").forEach(control => control.addEventListener("input", renderScenarioCatalog));
    scenarioSelect.addEventListener("change", renderScenarioMetadata);
    form.querySelector("[data-apply-scenario]").addEventListener("click", async () => {
      if (!scenarioSelect.value) return setScenarioStatus("Select a saved scenario first.", "error");
      try {
        const response = await fetch(`/api/v1/scenarios/${scenarioSelect.value}?${new URLSearchParams({service_name: selectedServiceName()})}`);
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.detail || "Could not load scenario");
        applyScenario(payload);
      } catch (error) { setScenarioStatus(error.message, "error"); }
    });
    form.querySelector("[data-scenario-import-file]").addEventListener("change", async event => {
      const file = event.target.files[0];
      if (!file) return;
      if (file.size > 256 * 1024) return setScenarioStatus("Scenario document exceeds the 256 KiB limit.", "error");
      form.querySelector("[data-scenario-import]").value = await file.text();
    });
    form.querySelector("[data-validate-scenario]").addEventListener("click", async () => {
      const content = form.querySelector("[data-scenario-import]").value.trim();
      if (!content) return setScenarioStatus("Paste or upload a scenario document first.", "error");
      try { await validateImportedScenario(content); }
      catch (error) { setScenarioStatus(error.message, "error"); }
    });
    form.elements.save_scenario.addEventListener("change", () => {
      const replacing = form.elements.save_scenario.value === "replace";
      form.querySelector("[data-replace-confirm]").hidden = !replacing;
      form.elements.replace_scenario.required = replacing;
      if (!replacing) form.elements.replace_scenario.checked = false;
    });
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
      try { serializeJourneys(); }
      catch (error) { event.preventDefault(); window.alert(error.message); }
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
      const kubernetes = data.get("execution_mode") === "kubernetes";
      const value = !kubernetes && ["kubernetes_context", "namespace"].includes(key) ? "" : data.get(key);
      const fallback = key === "service_name" ? "Inferred"
        : key === "workload_name" ? "Inferred during planning"
        : ["kubernetes_context", "namespace"].includes(key) ? "Not applicable"
        : key === "repo" && data.get("target_source") === "kubernetes" ? "Not required"
        : "—";
      node.textContent = value || fallback;
    });
    const journeys = form.querySelectorAll("[data-journey]");
    const adapters = new Set([...journeys].map(card => card.querySelector('[data-journey-field="adapter"]').value));
    const summary = form.querySelector("[data-review-journeys]");
    if (summary) summary.textContent = `${journeys.length} ${[...adapters].join(" + ").toUpperCase()} journey${journeys.length === 1 ? "" : "s"}`;
    let serialized = [];
    try { serialized = JSON.parse(form.querySelector("[data-journeys-json]").value || "[]"); }
    catch (_) { serialized = []; }
    let maxVus = 0;
    let durationSeconds = 0;
    const durationValue = value => {
      const match = String(value).match(/^(\d+)(ms|s|m|h)$/);
      if (!match) return 0;
      const unit = {ms: .001, s: 1, m: 60, h: 3600}[match[2]];
      return Number(match[1]) * unit;
    };
    serialized.forEach(journey => {
      maxVus = Math.max(maxVus, Number(journey.vus || 0));
      durationSeconds += Number(journey.durationSeconds || 0);
      (journey.stages || []).forEach(stage => {
        maxVus = Math.max(maxVus, Number(stage.targetVus || 0));
        durationSeconds += durationValue(stage.duration);
      });
    });
    form.querySelector("[data-review-vus]").textContent = `${maxVus} VUs`;
    form.querySelector("[data-review-duration]").textContent = durationSeconds ? `${durationSeconds}s` : "Not specified";
    const fault = data.get("fault_type");
    form.querySelector("[data-review-rollback]").textContent = fault === "none" ? "Not required" : "Required and verified after injection";
    let signals = [];
    let warnings = [];
    try { signals = JSON.parse(data.get("required_signals_json") || "[]"); } catch (_) { signals = []; }
    try { warnings = JSON.parse(form.dataset.scenarioWarnings || "[]"); } catch (_) { warnings = []; }
    form.querySelector("[data-review-signals]").textContent = signals.join(", ") || "default Chamber evidence";
    form.querySelector("[data-review-limitations]").textContent = warnings.join(" ") || "none";
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
