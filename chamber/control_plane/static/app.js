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
    const basicBuilder = form.querySelector("[data-basic-builder]");
    const basicMethod = form.querySelector("[data-basic-method]");
    const basicPath = form.querySelector("[data-basic-path]");
    const basicStatus = form.querySelector("[data-basic-status]");
    const basicVus = form.querySelector("[data-basic-vus]");
    const basicDuration = form.querySelector("[data-basic-duration]");
    const goalStatus = form.querySelector("[data-goal-status]");
    const csrfToken = form.elements._csrf.value;
    let discoveryData = null;
    let repositoryInspection = null;
    let repositoryInspectionPath = "";
    let scenarioCatalog = [];
    let scenarioWarnings = [];
    let goalProposal = null;
    let goalContextDirty = true;
    let current = 0;

    const profiles = {
      smoke: [{duration: "15s", targetVus: 1}, {duration: "5s", targetVus: 0}],
      baseline: [{duration: "30s", targetVus: 4}, {duration: "30s", targetVus: 0}],
      stress: [{duration: "30s", targetVus: 10}, {duration: "60s", targetVus: 25}, {duration: "30s", targetVus: 0}],
    };

    const field = (card, name) => card.querySelector(`[data-journey-field="${name}"]`);
    const multipartField = (row, name) => row.querySelector(`[data-multipart-file-field="${name}"]`);
    const originalJourney = card => {
      try {
        const item = JSON.parse(card.dataset.journeyBase || "{}");
        return item && typeof item === "object" && !Array.isArray(item) ? item : {};
      } catch (_) { return {}; }
    };
    const retainedMultipartFile = row => {
      try {
        const item = JSON.parse(row.dataset.retainedMultipartFile || "null");
        return item && typeof item === "object" ? item : null;
      } catch (_) { return null; }
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

    const syncMultipartFiles = (card, active) => {
      const rows = [...card.querySelectorAll("[data-multipart-file]")];
      rows.forEach(row => {
        const upload = multipartField(row, "file");
        const retained = retainedMultipartFile(row);
        const usingRetained = retained && !upload.files.length;
        upload.disabled = !active;
        upload.required = active && multipartField(row, "required").checked && !usingRetained;
        const existingFileStatus = row.querySelector("[data-existing-multipart-file]");
        existingFileStatus.hidden = !active || !usingRetained;
        existingFileStatus.textContent = usingRetained
          ? `Using validated file: ${retained.filename || retained.path}. Select a new upload to replace it.`
          : "";
        row.querySelector("[data-remove-multipart-file]").disabled = rows.length === 1;
      });
    };

    const addMultipartFile = (card, {fieldName, item = null} = {}) => {
      const template = card.querySelector("[data-multipart-file-template]");
      const fragment = template.content.cloneNode(true);
      const row = fragment.querySelector("[data-multipart-file]");
      const count = card.querySelectorAll("[data-multipart-file]").length + 1;
      multipartField(row, "field").value = fieldName || item?.field || (count === 1 ? "file" : `file_${count}`);
      multipartField(row, "filename").value = item?.filename || "";
      multipartField(row, "contentType").value = item?.contentType || "";
      multipartField(row, "required").checked = item?.required !== false;
      if (item?.path && item?.pathToken) {
        row.dataset.retainedMultipartFile = JSON.stringify(item);
      }
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
      card.querySelector("[data-add-multipart-file]").addEventListener("click", () => addMultipartFile(card));
      card.querySelector("[data-remove-journey]").addEventListener("click", () => {
        card.remove();
        renumberJourneys();
      });
      addMultipartFile(card);
      journeyList.appendChild(fragment);
      syncJourney(card, {resetDefaults: adapter === "relayna"});
      if (journey) populateJourney(card, journey);
      renumberJourneys();
      return card;
    };

    const populateJourney = (card, journey) => {
      card.dataset.journeyBase = JSON.stringify(journey);
      const adapter = journey.adapter || "http";
      field(card, "adapter").value = adapter;
      field(card, "name").value = journey.name || "traffic";
      field(card, "method").value = (journey.method || "GET").toUpperCase();
      field(card, "path").value = journey.path || "/health";
      field(card, "expectedStatus").value = journey.expectedStatus || 200;
      field(card, "tool").value = journey.tool || "k6";
      const encoding = journey.requestEncoding || (Object.hasOwn(journey, "body") ? "json" : "none");
      field(card, "requestEncoding").value = encoding;
      if (encoding === "json") {
        field(card, "body").value = Object.hasOwn(journey, "body")
          ? JSON.stringify(journey.body, null, 2)
          : "";
      }
      if (encoding === "multipart") {
        const files = Array.isArray(journey.multipart?.files) ? journey.multipart.files : [];
        field(card, "multipartFields").value = JSON.stringify(journey.multipart?.fields || {}, null, 2);
        card.querySelector("[data-multipart-file-list]").replaceChildren();
        if (files.length) {
          files.forEach(item => addMultipartFile(card, {fieldName: item?.field, item}));
        } else {
          addMultipartFile(card);
        }
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
      form.elements.agents_exclude_json.value = JSON.stringify(projection.agentExclusions || [], null, 2);
      if (Number.isInteger(projection.targetServicePort)) {
        form.elements.service_port.value = projection.targetServicePort;
      }
      form.elements.fault_type.value = "none";
      journeyList.replaceChildren();
      projection.journeys.forEach(journey => addJourney({adapter: journey.adapter || "http", journey}));
      scenarioWarnings = projection.warnings || [];
      form.dataset.scenarioWarnings = JSON.stringify(scenarioWarnings);
      goalProposal = null;
      goalContextDirty = false;
      setEditorMode("advanced");
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
        const baseJourney = originalJourney(card);
        const journey = {
          ...baseJourney,
          name: field(card, "name").value.trim(),
          method: field(card, "method").value,
          path: field(card, "path").value.trim(),
          expectedStatus: Number(field(card, "expectedStatus").value),
          requestEncoding,
        };
        delete journey.adapter;
        delete journey.body;
        delete journey.form;
        delete journey.multipart;
        delete journey.contentType;
        delete journey.textBytes;
        delete journey.stages;
        delete journey.vus;
        delete journey.iterations;
        delete journey.durationSeconds;
        delete journey.relayna;
        delete journey.followUps;
        const tool = field(card, "tool").value.trim();
        if (tool) journey.tool = tool; else delete journey.tool;
        if (adapter === "relayna") journey.adapter = "relayna";
        if (requestEncoding === "json") {
          const bodyControl = field(card, "body");
          const body = parseJson(bodyControl, `Journey ${index + 1} request body`, {required: adapter === "relayna"});
          if (bodyControl.value.trim()) journey.body = body;
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
            const retained = retainedMultipartFile(row);
            const required = multipartField(row, "required").checked;
            const fileField = multipartField(row, "field").value.trim();
            if (!fileField) throw new Error(`Journey ${index + 1} file ${fileIndex + 1} requires a field name.`);
            if (required && !upload && !retained) throw new Error(`Journey ${index + 1} required file ${fileField} needs an upload.`);
            uploadControl.disabled = prepareUploads && !upload;
            const item = {field: fileField, required};
            if (upload) {
              const filename = multipartField(row, "filename").value.trim() || upload.name;
              const contentType = multipartField(row, "contentType").value.trim() || upload.type;
              if (!contentType) throw new Error(`Journey ${index + 1} file ${fileField} requires a content type.`);
              Object.assign(item, {filename, contentType, uploadIndex});
              uploadIndex += 1;
            } else if (retained) {
              Object.assign(item, {
                path: retained.path,
                pathToken: retained.pathToken,
                filename: multipartField(row, "filename").value.trim() || retained.filename,
                contentType: multipartField(row, "contentType").value.trim() || retained.contentType,
              });
            }
            return item;
          });
          const baseMultipart = baseJourney.multipart && typeof baseJourney.multipart === "object"
            ? baseJourney.multipart : {};
          journey.multipart = {
            ...baseMultipart,
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
          const baseLifecycle = baseJourney.relayna && typeof baseJourney.relayna === "object"
            ? baseJourney.relayna : {};
          journey.relayna = {
            ...baseLifecycle,
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
      goalContextDirty = true;
    };

    const applyService = () => {
      const service = (discoveryData?.services || []).find(item => item.name === discoveredService.value);
      if (!service) return;
      goalContextDirty = true;
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

    const inspectRepositoryForGoal = async () => {
      const attached = form.querySelector('input[name="target_source"][value="kubernetes"]').checked;
      const selectedRepo = repo.value.trim();
      if (attached || !selectedRepo) return null;
      if (repositoryInspection && repositoryInspectionPath === selectedRepo) return repositoryInspection;
      const response = await fetch("/api/v1/inspect", {
        method: "POST",
        headers: {"Content-Type": "application/json", "X-CSRF-Token": csrfToken},
        body: JSON.stringify({repo: selectedRepo}),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || "Repository inspection failed");
      repositoryInspection = payload;
      repositoryInspectionPath = selectedRepo;
      const inspectedService = payload.service?.name;
      if (!serviceName.value.trim() && inspectedService) serviceName.value = inspectedService;
      const workload = (payload.deployment?.workloads || []).find(item => item.role === "target")
        || (payload.deployment?.workloads || [])[0];
      if (!workloadName.value.trim() && workload?.name) {
        workloadName.value = workload.name;
        if (workload.kind) form.elements.workload_kind.value = workload.kind;
      }
      return payload;
    };

    const proposalContext = async goal => {
      let inspection = null;
      try { inspection = await inspectRepositoryForGoal(); }
      catch (error) { goalStatus.textContent = `${error.message}. Using the entered values instead.`; }
      const dependencies = [
        ...(inspection?.dependencies?.internal || []),
        ...(inspection?.dependencies?.external || []),
      ].map(item => typeof item === "string" ? item : item?.name).filter(Boolean);
      const inspectedPath = inspection?.traffic?.journeys?.[0]?.path || "";
      const attached = form.querySelector('input[name="target_source"][value="kubernetes"]').checked;
      return {
        goal,
        service_name: serviceName.value.trim(),
        workload_name: workloadName.value.trim(),
        service_port: Number(form.elements.service_port.value) || null,
        request_path: inspectedPath,
        repository_available: Boolean(inspection),
        attach_mode: attached,
        discovery_complete: Boolean(attached && discoveryData && serviceName.value.trim() && workloadName.value.trim()),
        dependency_names: dependencies,
        telemetry_available: form.elements.prometheus_url.value.trim() ? ["prometheus"] : [],
      };
    };

    const renderList = (selector, values, emptyMessage) => {
      const list = form.querySelector(selector);
      const items = values.length ? values : [emptyMessage];
      list.replaceChildren(...items.map(value => {
        const item = document.createElement("li");
        item.textContent = value;
        return item;
      }));
    };

    const proposalDuration = journeys => journeys.reduce((total, journey) => total
      + Number(journey.durationSeconds || 0)
      + (journey.stages || []).reduce((stageTotal, stage) => {
        const match = String(stage.duration || "").match(/^(\d+)(s|m)$/);
        return stageTotal + (match ? Number(match[1]) * (match[2] === "m" ? 60 : 1) : 0);
      }, 0), 0);

    const renderGoalSummary = () => {
      let journeys = [];
      try { journeys = serializeJourneys(); } catch (_) { journeys = []; }
      const maxVus = journeys.reduce((maximum, journey) => Math.max(
        maximum,
        Number(journey.vus || 0),
        ...(journey.stages || []).map(stage => Number(stage.targetVus || 0)),
      ), 0);
      const duration = proposalDuration(journeys);
      const selectedFault = form.elements.fault_type.value;
      const faultLabel = form.elements.fault_type.selectedOptions[0]?.textContent || "Observe only";
      form.querySelector("[data-goal-max-vus]").textContent = `${maxVus} VUs`;
      form.querySelector("[data-goal-duration]").textContent = duration ? `${duration}s` : "Not specified";
      form.querySelector("[data-goal-fault]").textContent = selectedFault === "none" ? "No fault selected" : faultLabel;
      form.querySelector("[data-goal-safety]").textContent = `${maxVus}/25 VUs · ${duration}/300s`;
      const timeline = [
        ["Traffic", `${maxVus} VUs maximum across ${duration || "an unspecified"}s`],
        ["Fault", selectedFault === "none" ? (goalProposal?.faultSummary || "No fault selected") : faultLabel],
        ["Recovery", "Traffic returns to zero and readiness evidence is collected"],
      ];
      form.querySelector("[data-goal-timeline]").replaceChildren(...timeline.map(([label, detail], index) => {
        const item = document.createElement("li");
        const marker = document.createElement("span");
        marker.textContent = String(index + 1);
        const content = document.createElement("div");
        const heading = document.createElement("strong");
        const note = document.createElement("small");
        heading.textContent = label;
        note.textContent = detail;
        content.append(heading, note);
        item.append(marker, content);
        return item;
      }));
      renderList("[data-goal-outcomes]", goalProposal?.expectedOutcomes || [], "Confirm the expected outcome in Advanced mode.");
      renderList("[data-goal-evidence]", goalProposal?.requiredEvidence || [], "Default Chamber evidence");
      renderList("[data-goal-assumptions]", goalProposal?.assumptions || [], "No generated assumptions yet.");
      renderList("[data-goal-missing]", goalProposal?.missingInputs || [], "No unresolved inputs.");
      const first = journeys[0] || {};
      const requestPreview = `${first.method || "GET"} http://${serviceName.value.trim() || "<service>"}:${form.elements.service_port.value || "<port>"}${first.path || "/"}\nExpected status: ${first.expectedStatus || "<required>"}\nEncoding: ${first.requestEncoding || "none"}`;
      form.querySelector("[data-goal-request-preview]").textContent = requestPreview;
      form.querySelector("[data-goal-config-preview]").textContent = JSON.stringify({
        goal: form.elements.reliability_goal.value,
        target: {
          service: serviceName.value.trim() || "<missing>",
          workload: workloadName.value.trim() || "<missing>",
        },
        traffic: {journeys},
        expectedOutcomes: goalProposal?.expectedOutcomes || [],
        requiredEvidence: goalProposal?.requiredEvidence || [],
        fault: selectedFault,
        safety: {maxVirtualUsers: maxVus, maxDurationSeconds: duration, faultsRequireExplicitSelection: true},
      }, null, 2);
    };

    const syncBasicControls = () => {
      const card = journeyList.querySelector("[data-journey]");
      if (!card) return;
      basicMethod.value = field(card, "method").value;
      basicPath.value = field(card, "path").value;
      basicStatus.value = field(card, "expectedStatus").value;
      let journey = null;
      try { journey = serializeJourneys()[0]; } catch (_) { journey = null; }
      const vus = Math.max(Number(journey?.vus || 0), ...(journey?.stages || []).map(stage => Number(stage.targetVus || 0)));
      basicVus.value = String(vus || 1);
      basicDuration.value = String(proposalDuration(journey ? [journey] : []) || 20);
    };

    const applyBasicEdits = () => {
      const card = journeyList.querySelector("[data-journey]");
      if (!card) return;
      field(card, "method").value = basicMethod.value;
      field(card, "path").value = basicPath.value;
      field(card, "expectedStatus").value = basicStatus.value;
      if (field(card, "adapter").value === "http") {
        const duration = Math.max(20, Math.min(300, Number(basicDuration.value) || 20));
        const vus = Math.max(1, Math.min(25, Number(basicVus.value) || 1));
        field(card, "loadModel").value = "stages";
        field(card, "stages").value = JSON.stringify([
          {duration: `${duration - 10}s`, targetVus: vus},
          {duration: "10s", targetVus: 0},
        ], null, 2);
      }
      syncJourney(card);
      renderGoalSummary();
    };

    const applyGoalProposal = projection => {
      goalProposal = projection;
      form.elements.fault_type.value = "none";
      form.elements.scenario_id.value = `${(serviceName.value.trim() || "service").toLowerCase().replace(/[^a-z0-9.-]+/g, "-")}-${projection.goal.replaceAll("_", "-")}`.slice(0, 63).replace(/[-.]$/, "");
      form.elements.scenario_name.value = `${serviceName.value.trim() || "Service"} · ${projection.label}`;
      form.elements.scenario_description.value = projection.description;
      form.elements.scenario_tags.value = `goal-first, ${projection.goal.replaceAll("_", "-")}`;
      form.elements.scenario_source.value = "custom";
      form.elements.scenario_revision.value = "";
      form.elements.required_signals_json.value = JSON.stringify(projection.requiredEvidence);
      scenarioWarnings = projection.missingInputs;
      form.dataset.scenarioWarnings = JSON.stringify(scenarioWarnings);
      journeyList.replaceChildren();
      projection.journeys.forEach(journey => addJourney({adapter: journey.adapter || "http", journey}));
      syncBasicControls();
      renderGoalSummary();
      const gap = projection.missingInputs.length;
      goalStatus.textContent = gap
        ? `Proposal ready with ${gap} missing input${gap === 1 ? "" : "s"}. Resolve or accept each assumption before Review.`
        : "Proposal ready from the discovered values. Review the assumptions before continuing.";
      goalStatus.classList.remove("error");
      goalStatus.classList.toggle("warning", gap > 0);
    };

    const requestGoalProposal = async goal => {
      form.elements.fault_type.value = "none";
      goalStatus.textContent = "Building a bounded proposal from discovered values…";
      goalStatus.classList.remove("error", "warning");
      try {
        const response = await fetch("/api/v1/scenarios/propose", {
          method: "POST",
          headers: {"Content-Type": "application/json", "X-CSRF-Token": csrfToken},
          body: JSON.stringify(await proposalContext(goal)),
        });
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.detail || "Could not build goal proposal");
        applyGoalProposal(payload);
        goalContextDirty = false;
      } catch (error) {
        goalStatus.textContent = error.message;
        goalStatus.classList.add("error");
      }
    };

    const setEditorMode = mode => {
      basicBuilder.hidden = mode !== "basic";
      form.querySelectorAll("[data-advanced-only]").forEach(node => node.hidden = mode !== "advanced");
      form.querySelectorAll("[data-editor-mode]").forEach(button => {
        const active = button.dataset.editorMode === mode;
        button.classList.toggle("active", active);
        button.setAttribute("aria-pressed", String(active));
      });
      if (mode === "basic") {
        syncBasicControls();
        renderGoalSummary();
      }
    };

    const ensureScenarioIdentity = () => {
      const label = serviceName.value.trim() || "Service";
      if (!form.elements.scenario_id.value.trim()) {
        const base = (serviceName.value.trim() || repo.value.split("/").filter(Boolean).at(-1) || "service")
          .toLowerCase().replaceAll("_", "-").replace(/[^a-z0-9.-]+/g, "-").replace(/^-+|-+$/g, "");
        form.elements.scenario_id.value = `${base || "service"}-assessment`.slice(0, 63).replace(/[-.]$/, "");
      }
      if (!form.elements.scenario_name.value.trim()) {
        form.elements.scenario_name.value = `${label} reliability assessment`;
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
      if (current >= 2) ensureScenarioIdentity();
      if (current === 2 && !basicBuilder.hidden && goalContextDirty && form.elements.scenario_source.value === "custom") {
        requestGoalProposal(form.elements.reliability_goal.value);
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
    form.querySelectorAll("[data-editor-mode]").forEach(button => button.addEventListener("click", () => setEditorMode(button.dataset.editorMode)));
    form.querySelectorAll('input[name="reliability_goal"]').forEach(input => input.addEventListener("change", () => {
      selectModeCard(input);
      requestGoalProposal(input.value);
    }));
    [basicMethod, basicPath, basicStatus, basicVus, basicDuration].forEach(control => control.addEventListener("input", applyBasicEdits));
    form.elements.fault_type.addEventListener("change", renderGoalSummary);
    [serviceName, workloadName].forEach(control => control.addEventListener("input", () => {
      goalContextDirty = true;
      renderGoalSummary();
    }));
    form.elements.service_port.addEventListener("input", renderGoalSummary);
    repo.addEventListener("input", () => {
      repositoryInspection = null;
      repositoryInspectionPath = "";
      goalContextDirty = true;
    });
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
      goalContextDirty = true;
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
    submit.addEventListener("click", ensureScenarioIdentity);
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
    setEditorMode("basic");
    render();
  }

  function updateReview(form) {
    const data = new FormData(form);
    form.querySelectorAll("[data-review]").forEach(node => {
      const key = node.dataset.review;
      const kubernetes = data.get("execution_mode") === "kubernetes";
      const selectedGoal = form.querySelector('input[name="reliability_goal"]:checked');
      const value = key === "reliability_goal" ? selectedGoal?.closest("label")?.querySelector("strong")?.textContent
        : !kubernetes && ["kubernetes_context", "namespace"].includes(key) ? "" : data.get(key);
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
    const fileDescriptions = [...form.querySelectorAll("[data-multipart-file]")].flatMap(row => {
      const card = row.closest("[data-journey]");
      if (card.querySelector('[data-journey-field="requestEncoding"]').value !== "multipart") return [];
      const upload = row.querySelector('[data-multipart-file-field="file"]').files[0];
      if (upload) return [`${upload.name} (${upload.type || "unknown type"}, ${upload.size} bytes)`];
      try {
        const retained = JSON.parse(row.dataset.retainedMultipartFile || "null");
        return retained ? [`${retained.filename || retained.path} (${retained.contentType || "unknown type"}, retained upload)`] : [];
      } catch (_) { return []; }
    });
    const fileSummary = form.querySelector("[data-review-files]");
    if (fileSummary) {
      fileSummary.textContent = fileDescriptions.length
        ? `${fileDescriptions.length} file${fileDescriptions.length === 1 ? "" : "s"}: ${fileDescriptions.join(", ")}`
        : "No multipart uploads";
    }
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
    const outcomeSummary = form.querySelector("[data-review-outcomes]");
    if (outcomeSummary) outcomeSummary.textContent = (form.querySelector("[data-goal-outcomes] li")?.textContent || "Review the configured journey outcomes");
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
