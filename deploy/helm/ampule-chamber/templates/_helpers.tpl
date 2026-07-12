{{- define "ampule-chamber.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "ampule-chamber.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name (include "ampule-chamber.name" .) | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}

{{- define "ampule-chamber.labels" -}}
app.kubernetes.io/name: {{ include "ampule-chamber.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{- define "ampule-chamber.selectorLabels" -}}
app.kubernetes.io/name: {{ include "ampule-chamber.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{- define "ampule-chamber.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "ampule-chamber.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- required "serviceAccount.name is required when serviceAccount.create=false" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{- define "ampule-chamber.authSecretName" -}}
{{- default (printf "%s-auth" (include "ampule-chamber.fullname" .)) .Values.auth.existingSecret }}
{{- end }}

{{- define "ampule-chamber.workspaceClaimName" -}}
{{- default (printf "%s-workspace" (include "ampule-chamber.fullname" .)) .Values.persistence.existingClaim }}
{{- end }}
