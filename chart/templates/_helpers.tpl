{{- define "sre-reflex.fullname" -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "sre-reflex.selectorLabels" -}}
app.kubernetes.io/name: sre-reflex
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "sre-reflex.labels" -}}
{{ include "sre-reflex.selectorLabels" . }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "sre-reflex.image" -}}
{{ required "image.repository is required" .Values.image.repository }}:{{ .Values.image.tag | default .Chart.AppVersion }}
{{- end -}}

{{- define "sre-reflex.env" -}}
- name: METRIC_QUERIES_FILE
  value: /config/metric-queries.yaml
{{- range $k, $v := .Values.env }}
- name: {{ $k }}
  value: {{ $v | quote }}
{{- end }}
{{- end -}}

{{- define "sre-reflex.envFrom" -}}
{{- with .Values.existingSecret }}
envFrom:
  - secretRef:
      name: {{ . }}
{{- end }}
{{- end -}}
