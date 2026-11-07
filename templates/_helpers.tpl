{{/*
Expand the name of the chart.
*/}}
{{- define "fixbot.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
*/}}
{{- define "fixbot.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}

{{/*
Resource name prefix for a specific Course (e.g., fixbot-example).
*/}}
{{- define "fixbot.coursePrefix" -}}
{{- printf "%s-%s" .Release.Name .slug }}
{{- end }}

{{/*
Chart name and version labels.
*/}}
{{- define "fixbot.labels" -}}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version | replace "+" "_" }}
app.kubernetes.io/name: {{ include "fixbot.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Course-specific labels.
*/}}
{{- define "fixbot.courseLabels" -}}
app.kubernetes.io/part-of: fixbot
fixbot.example.com/course: {{ .slug }}
{{- end }}

{{/*
Open WebUI environment variables from CNPG secret.
Uses DATABASE_URL constructed from the CNPG auto-generated secret.
*/}}
{{- define "fixbot.openWebUIEnv" -}}
- name: DATABASE_URL
  valueFrom:
    secretKeyRef:
      name: {{ include "fixbot.coursePrefix" . }}-db-app
      key: pgpass
- name: WEBUI_SECRET_KEY
  valueFrom:
    secretKeyRef:
      name: {{ include "fixbot.coursePrefix" . }}-db-app
      key: password
{{- if .aiass.enabled }}
{{- if .aiass.baseUrl }}
- name: OPENAI_API_BASE_URL
  value: {{ .aiass.baseUrl | quote }}
{{- end }}
{{- if .aiass.apiKeySecretName }}
- name: OPENAI_API_KEY
  valueFrom:
    secretKeyRef:
      name: {{ .aiass.apiKeySecretName }}
      key: api-key
{{- end }}
{{- end }}
{{- if .oidc.enabled }}
- name: ENABLE_OAUTH_SIGNUP
  value: "true"
- name: OPENID_PROVIDER_URL
  value: {{ .oidc.providerUrl | quote }}
- name: OAUTH_PROVIDER_NAME
  value: {{ .oidc.providerName | quote }}
- name: OAUTH_AUTO_REDIRECT
  value: {{ .oidc.autoRedirect | quote }}
- name: OAUTH_CLIENT_ID
  value: {{ .oidc.clientId | quote }}
{{- if .oidc.clientSecretSecretName }}
- name: OAUTH_CLIENT_SECRET
  valueFrom:
    secretKeyRef:
      name: {{ .oidc.clientSecretSecretName }}
      key: client-secret
{{- end }}
{{- if .oidc.disablePasswordAuth }}
- name: ENABLE_LOGIN_FORM
  value: "false"
- name: ENABLE_PASSWORD_AUTH
  value: "false"
{{- end }}
{{- end }}
{{- end }}

{{/*
Shared Postgres env (DB_HOST/DB_USER/DB_PASSWORD/DATABASE_URL/WEBUI_SECRET_KEY)
for the Open WebUI container AND the multi-replica migration init container.
DB_* must precede DATABASE_URL (Kubernetes expands $(VAR) only from earlier
entries). Expects ".prefix" (course resource prefix).
*/}}
{{- define "fixbot.webuiDbEnv" -}}
- name: DB_HOST
  value: "{{ .prefix }}-db-rw"
- name: DB_USER
  valueFrom:
    secretKeyRef:
      name: {{ .prefix }}-db-app
      key: username
- name: DB_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ .prefix }}-db-app
      key: password
- name: DATABASE_URL
  value: "postgresql://$(DB_USER):$(DB_PASSWORD)@$(DB_HOST):5432/fixbot?sslmode=require"
- name: WEBUI_SECRET_KEY
  valueFrom:
    secretKeyRef:
      name: {{ .prefix }}-db-app
      key: password
{{- end }}

{{/*
Pipe State DB env (STATE_DB_HOST/STATE_DB_USER/STATE_DB_PASSWORD/
FIXBOT_STATE_DB_URL) for the Open WebUI container. Mirrors fixbot.webuiDbEnv:
STATE_DB_* must precede FIXBOT_STATE_DB_URL (Kubernetes expands $(VAR) only
from earlier entries). The Pipe code running inside Open WebUI uses
FIXBOT_STATE_DB_URL to persist Pipe Session state (ADR 0005).
Expects ".prefix" (course resource prefix) and ".database".
*/}}
{{- define "fixbot.stateDbEnv" -}}
- name: STATE_DB_HOST
  value: "{{ .prefix }}-state-db-rw"
- name: STATE_DB_USER
  valueFrom:
    secretKeyRef:
      name: {{ .prefix }}-state-db-app
      key: username
- name: STATE_DB_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ .prefix }}-state-db-app
      key: password
- name: FIXBOT_STATE_DB_URL
  value: "postgresql://$(STATE_DB_USER):$(STATE_DB_PASSWORD)@$(STATE_DB_HOST):5432/{{ .database }}?sslmode=require"
{{- end }}

{{/*
Merge Course config with defaults. Returns a dict with merged values.
Usage: {{ include "fixbot.courseConfig" (dict "root" $ "slug" "example" "course" .Values.courses.example) }}
*/}}
{{- define "fixbot.courseConfig" -}}
{{- $root := .root }}
{{- $slug := .slug }}
{{- $course := .course }}
{{- $owDefaults := $root.Values.openWebUI }}
{{- $pgDefaults := $root.Values.postgres }}
{{- $aiassDefaults := $root.Values.aiass }}
{{- $oidcDefaults := $root.Values.oidc }}
{{- $backupDefaults := $root.Values.backup }}
{{- $redisDefaults := $root.Values.redis }}
{{- $vectorDefaults := $root.Values.vectorDB }}
{{- $provDefaults := $owDefaults.provisioning | default dict }}
{{- $provCourse := $course.provisioning | default dict }}
{{- $provSettingsDefaults := $provDefaults.settings | default dict }}
{{- $provSettingsCourse := $provCourse.settings | default dict }}
{{- $provSA := mergeOverwrite (deepCopy (default dict $provDefaults.serviceAccount)) (default dict $provCourse.serviceAccount) }}
{{- $sdDefaults := $root.Values.stateDb | default dict }}
{{- $sdCourse := $course.stateDb | default dict }}
{{- $sdBackupDefaults := $sdDefaults.backup | default dict }}
{{- $sdBackupCourse := $sdCourse.backup | default dict }}
{{- $sdEnabled := true }}
{{- if hasKey $sdCourse "enabled" }}
{{- $sdEnabled = $sdCourse.enabled }}
{{- else }}
{{- $sdEnabled = $sdDefaults.enabled }}
{{- end }}
{{- $sdBackupEnabled := false }}
{{- if $sdEnabled }}
{{- if hasKey $sdBackupCourse "enabled" }}
{{- $sdBackupEnabled = $sdBackupCourse.enabled }}
{{- else }}
{{- $sdBackupEnabled = $sdBackupDefaults.enabled }}
{{- end }}
{{- end }}
{{- $qkbDefaults := $root.Values.qkb | default dict }}
{{- $qkbCourse := $course.qkb | default dict }}
{{- $qkbRepo := default (printf "%s-%v" $slug $qkbDefaults.year) $qkbCourse.repo }}
{{- $admins := (default (list) $owDefaults.admins) }}
{{- if hasKey $course "admins" }}
{{- $admins = (append $admins (default (list) $course.admins)) | uniq }}
{{- end }}
{{- $ow := $course.openWebUI | default dict }}
{{- $owReplicas := default $owDefaults.replicas $ow.replicas }}
{{- $ha := false }}
{{- if gt (int $owReplicas) 1 }}{{- $ha = true }}{{- end }}
{{- $redis := mergeOverwrite (deepCopy $redisDefaults) ($course.redis | default dict) }}
{{- $vector := mergeOverwrite (deepCopy $vectorDefaults) ($course.vectorDB | default dict) }}
---
slug: {{ $slug }}
ha: {{ $ha }}
openWebUI:
  {{- $ow := $course.openWebUI | default dict }}
  {{- $res := $ow.resources | default dict }}
  {{- $req := $res.requests | default dict }}
  {{- $lim := $res.limits | default dict }}
  replicas: {{ $owReplicas }}
  selfSignup: {{ $ow.selfSignup | default $owDefaults.selfSignup }}
  resources:
    requests:
      cpu: {{ default $owDefaults.resources.requests.cpu $req.cpu }}
      memory: {{ default $owDefaults.resources.requests.memory $req.memory }}
    limits:
      cpu: {{ default $owDefaults.resources.limits.cpu $lim.cpu }}
      memory: {{ default $owDefaults.resources.limits.memory $lim.memory }}
redis:
  replicas: {{ $redis.replicas }}
  resources:
{{ toYaml $redis.resources | indent 4 }}
vectorDB:
  replicas: {{ $vector.replicas }}
  image:
    repository: {{ $vector.image.repository }}
    tag: {{ $vector.image.tag }}
    pullPolicy: {{ $vector.image.pullPolicy }}
  resources:
{{ toYaml $vector.resources | indent 4 }}
admins: {{ $admins | toJson }}
provisioning:
  enabled: {{ default $provDefaults.enabled $provCourse.enabled }}
  serviceAccount:
    email: {{ $provSA.email | default "" | quote }}
    name: {{ $provSA.name | default "" | quote }}
  settings:
    defaultModels: {{ (default $provSettingsDefaults.defaultModels $provSettingsCourse.defaultModels) | default "" | quote }}
    defaultPinnedModels: {{ (default $provSettingsDefaults.defaultPinnedModels $provSettingsCourse.defaultPinnedModels) | default "" | quote }}
    modelOrderList: {{ (default $provSettingsDefaults.modelOrderList $provSettingsCourse.modelOrderList) | default list | toJson }}
    defaultModelMetadata: {{ mergeOverwrite (deepCopy (default dict $provSettingsDefaults.defaultModelMetadata)) (default dict $provSettingsCourse.defaultModelMetadata) | toJson }}
    defaultModelParams: {{ mergeOverwrite (deepCopy (default dict $provSettingsDefaults.defaultModelParams)) (default dict $provSettingsCourse.defaultModelParams) | toJson }}
postgres:
  {{- $pg := $course.postgres | default dict }}
  {{- $pgres := $pg.resources | default dict }}
  {{- $pgreq := $pgres.requests | default dict }}
  {{- $pglim := $pgres.limits | default dict }}
  instances: {{ default $pgDefaults.instances $pg.instances }}
  storageClass: {{ default $pgDefaults.storageClass $pg.storageClass }}
  storageSize: {{ default $pgDefaults.storageSize $pg.storageSize }}
  podAntiAffinityType: {{ default $pgDefaults.podAntiAffinityType $pg.podAntiAffinityType }}
  maxConnections: {{ default $pgDefaults.maxConnections $pg.maxConnections }}
  resources:
    requests:
      cpu: {{ default $pgDefaults.resources.requests.cpu $pgreq.cpu }}
      memory: {{ default $pgDefaults.resources.requests.memory $pgreq.memory }}
    limits:
      cpu: {{ default $pgDefaults.resources.limits.cpu $pglim.cpu }}
      memory: {{ default $pgDefaults.resources.limits.memory $pglim.memory }}
stateDb:
  enabled: {{ $sdEnabled }}
  {{- if $sdEnabled }}
  instances: {{ default $sdDefaults.instances $sdCourse.instances }}
  imageName: {{ (default (default $root.Values.postgres.imageName $sdDefaults.imageName) $sdCourse.imageName) | quote }}
  storageClass: {{ default $sdDefaults.storageClass $sdCourse.storageClass }}
  storageSize: {{ default $sdDefaults.storageSize $sdCourse.storageSize }}
  database: {{ default $sdDefaults.database $sdCourse.database }}
  owner: {{ default $sdDefaults.owner $sdCourse.owner }}
  maxConnections: {{ default $sdDefaults.maxConnections $sdCourse.maxConnections }}
  {{- $sdRes := $sdCourse.resources | default dict }}
  {{- $sdReq := $sdRes.requests | default dict }}
  {{- $sdLim := $sdRes.limits | default dict }}
  resources:
    requests:
      cpu: {{ default $sdDefaults.resources.requests.cpu $sdReq.cpu }}
      memory: {{ default $sdDefaults.resources.requests.memory $sdReq.memory }}
    limits:
      cpu: {{ default $sdDefaults.resources.limits.cpu $sdLim.cpu }}
      memory: {{ default $sdDefaults.resources.limits.memory $sdLim.memory }}
  backup:
    enabled: {{ $sdBackupEnabled }}
  {{- end }}
qkb:
  repo: {{ $qkbRepo }}
aiass:
  {{- if hasKey $course "aiass" }}
  enabled: {{ $aiassDefaults.enabled }}
  baseUrl: {{ $aiassDefaults.baseUrl }}
  apiKeySecretName: {{ default "" $course.aiass.apiKeySecretName }}
  keyPoolSize: {{ default $aiassDefaults.keyPoolSize $course.aiass.keyPoolSize }}
  {{- else }}
  enabled: false
  baseUrl: ""
  apiKeySecretName: ""
  keyPoolSize: 1
  {{- end }}
webuiUrl: {{ printf "https://%s.%s" $slug $root.Values.domain }}
oidc:
  {{- if hasKey $course "oidc" }}
  enabled: {{ $oidcDefaults.enabled }}
  providerUrl: {{ default $oidcDefaults.providerUrl $course.oidc.providerUrl }}
  providerName: {{ $oidcDefaults.providerName }}
  autoRedirect: {{ $oidcDefaults.autoRedirect }}
  disablePasswordAuth: {{ if hasKey $course.oidc "disablePasswordAuth" }}{{ $course.oidc.disablePasswordAuth }}{{ else }}{{ $oidcDefaults.disablePasswordAuth }}{{ end }}
  clientId: {{ default $oidcDefaults.clientId $course.oidc.clientId }}
  clientSecretSecretName: {{ default $oidcDefaults.clientSecretSecretName $course.oidc.clientSecretSecretName }}
  scopes: {{ default $oidcDefaults.scopes $course.oidc.scopes | quote }}
  codeChallengeMethod: {{ default $oidcDefaults.codeChallengeMethod $course.oidc.codeChallengeMethod | quote }}
  mergeByEmail: {{ default $oidcDefaults.mergeByEmail $course.oidc.mergeByEmail }}
  {{- else }}
  enabled: false
  providerUrl: ""
  providerName: ""
  autoRedirect: false
  disablePasswordAuth: false
  clientId: ""
  clientSecretSecretName: ""
  scopes: ""
  codeChallengeMethod: ""
  mergeByEmail: false
  {{- end }}
{{- if $backupDefaults.enabled }}
backup:
  enabled: true
{{- end }}
{{- end }}

{{/*
Course hostname (e.g., example.fixbot.example.com).
*/}}
{{- define "fixbot.courseHostname" -}}
{{ .slug }}.{{ .root.Values.domain }}
{{- end }}
