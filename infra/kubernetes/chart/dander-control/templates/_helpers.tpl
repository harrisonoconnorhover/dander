{{- define "dander-control.labels" -}}
app.kubernetes.io/name: {{ .Chart.Name | quote }}
app.kubernetes.io/instance: {{ .Release.Name | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service | quote }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | quote }}
dander.io/profile: {{ .Values.profile | quote }}
{{- end }}

{{- define "dander-control.selector" -}}
app.kubernetes.io/name: {{ .Chart.Name | quote }}
app.kubernetes.io/instance: {{ .Release.Name | quote }}
{{- end }}
