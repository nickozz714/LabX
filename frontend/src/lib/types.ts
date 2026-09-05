export interface Lab {
  id: string;
  name: string;
  status: "creating" | "running" | "stopped" | "error" | "expired";
  image: string;
  network_alias: string | null;
  cpu_limit: number;
  mem_limit_mb: number;
  allow_network: boolean;
  ttl_hours: number;
  expires_at: string | null;
  repos: { name: string; url: string; source: string; authenticated: boolean }[];
  ports: number[];
  data_guard: boolean;
  llm_guard: boolean;
  allowed_mcp: string[];
  allowed_tools: string[];
  allowed_skills: string[];
  azure_profile_id: number | null;
  extras: string[];
  setup_script: string | null;
  provision_status: "pending" | "running" | "ok" | "error" | "skipped" | null;
  provision_log: ProvisionStep[];
  error: string | null;
  created_at: string;
  updated_at: string;
  last_used_at: string | null;
  port_map?: Record<string, number>;
}

/** Eén stap uit het inrichten van een lab (basisgereedschap, een extra, of het eigen script). */
export interface ProvisionStep {
  key: string;
  label: string;
  status: "ok" | "skipped" | "error";
  exit_code?: number | null;
  output?: string;
}

/** Een installeerbaar pakket uit de lab-catalogus (Instellingen > Lab-extra's). */
export interface LabExtra {
  id: number;
  key: string;
  label: string;
  description: string | null;
  check_cmd: string | null;
  install_script: string;
  requires: string[];
  /** Brengt dit pakket een MCP-server mee die in het lab draait? */
  mcp_server: { slug: string; name?: string; command: string; description?: string; replaces?: string[] } | null;
  timeout_s: number;
  default_on: boolean;
  is_enabled: boolean;
  builtin: boolean;
  sort_order: number;
  updated_at?: string;
}

export interface ImagePreset {
  key: string;
  label: string;
  image: string;
  description: string;
}

export interface DockerStatus {
  cli_present: boolean;
  daemon_up: boolean;
  in_container: boolean;
  socket_mounted: boolean;
  docker_host: string;
  network: string;
  hint: string | null;
}

export interface GuardModelStatus {
  model: string;
  url: string;
  local: boolean;
  reachable: boolean;
  ready: boolean;
  state: "ready" | "pulling" | "unreachable" | "non_local" | "disabled";
  hint?: string | null;
  in_docker?: boolean;
}

export interface Thread {
  id: string;
  title: string;
  lab_id: string;
  model: string | null;
  effort: string | null;
  created_at: string;
  updated_at: string;
}

export interface Message {
  id: string;
  thread_id: string;
  role: "user" | "assistant" | "tool";
  content: string;
  steps: ChatEvent[];
  created_at: string;
}

export type ChatEvent =
  | { kind: "session"; id: string }
  | { kind: "thinking"; text: string }
  | { kind: "tool"; name: string; input: any }
  | { kind: "delta"; text: string }
  | { kind: "answer"; text: string }
  | { kind: "run_status"; status: string }
  | { kind: "run"; id: string }
  | { kind: "usage"; input_tokens: number; output_tokens: number; cost_usd: number | null; duration_ms: number | null; num_turns?: number };

export interface BackgroundRunDto {
  id: string;
  thread_id: string;
  prompt: string;
  model: string | null;
  effort: string | null;
  status: "running" | "completed" | "failed" | "cancelled" | "interrupted";
  mode: "background" | "foreground";
  steps: ChatEvent[];
  answer: string | null;
  error: string | null;
  message_id: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface AutoHook {
  tool_name: string;
  query_template: string | null;
  instruction: string | null;
  enabled: boolean;
}

export interface AppSettingsDto {
  cli_path: string;
  oauth_token_configured: boolean;
  default_model: string;
  max_turns: number;
  timeout_seconds: number | null;
  extra_args: string[];
  enable_tool_search: boolean;
  data_guard_default: boolean;
  llm_guard_default: boolean;
  guard_llm_url: string;
  guard_llm_model: string;
  default_image: string;
  default_ttl_hours: number;
  auto_recall_enabled: boolean;
  auto_recall_tool_name: string | null;
  auto_recall_query_template: string | null;
  auto_recall_instruction: string | null;
  auto_hooks: AutoHook[];
  default_effort: string | null;
  fallback_model: string | null;
  max_budget_usd: number | null;
  autocompact: string | null;
  custom_agents_json: string | null;
  default_agent: string | null;
}

export interface MCPServerDto {
  id: number;
  name: string;
  slug: string;
  description: string | null;
  server_type: "http" | "sse" | "stdio" | "builtin";
  location: "host" | "lab";
  base_url: string | null;
  stdio_command: string | null;
  is_enabled: boolean;
  always_allowed: boolean;
  usage_scope: "session" | "lab" | "both";
  azure_profile_id: number | null;
  has_auth: boolean;
  /** Aparte inloggegevens voor het ophalen van de toolslijst; null/false =
   *  dezelfde als voor de aanroepen zelf. */
  sync_azure_profile_id: number | null;
  has_sync_auth: boolean;
  last_synced_at: string | null;
  last_sync_status: string | null;
  last_sync_error: string | null;
}

export interface ToolDto {
  id: number;
  name: string;
  remote_name: string;
  description: string | null;
  argument: Record<string, any>;
  output_schema: Record<string, any> | null;
  annotations: Record<string, any>;
  is_enabled: boolean;
  mcp_server_id: number | null;
  mcp_server: { id: number; name: string; slug: string; location: string } | null;
}

export interface SkillToolLink {
  link_id: number;
  tool_id: number;
  tool_name: string;
  tool_description: string | null;
  argument: Record<string, any>;
  mcp_server: { id: number; name: string; location: string } | null;
  is_enabled: boolean;
  instructions: string | null;
}

export interface SkillDto {
  id: number;
  name: string;
  display_name: string | null;
  description: string;
  instructions: string;
  input_schema: Record<string, any> | null;
  output_schema: Record<string, any> | null;
  is_system: boolean;
  is_enabled: boolean;
  priority: number;
  created_at: string;
  updated_at: string;
  tools: SkillToolLink[];
}

export interface WorkflowStep {
  index: number;
  title: string;
  instruction: string;
}

export interface WorkflowDto {
  id: number;
  name: string;
  description: string | null;
  markdown: string;
  steps: WorkflowStep[];
  is_enabled: boolean;
  created_at: string;
  updated_at: string;
}

export type ScheduleKind = "prompt" | "workflow" | "board";

export interface ScheduleDto {
  id: number;
  name: string;
  cron_expression: string;
  lab_id: string;
  kind: ScheduleKind;
  prompt: string | null;
  workflow_id: number | null;
  board_id: number | null;
  board_column: string | null;
  board_max_tickets: number;
  json_schema: string | null;
  is_enabled: boolean;
  last_run_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface ScheduleRunDto {
  id: string;
  scheduled_for: string;
  status: string;
  output: string | null;
  error: string | null;
  created_at: string;
  finished_at: string | null;
}

export interface AzureProfileDto {
  id: number;
  name: string;
  kind: "msal_bundle" | "service_principal" | "bearer";
  description: string | null;
  has_secret: boolean;
  identity: Record<string, any> | null;
  created_at: string;
  updated_at: string;
}


// ── Agent board ─────────────────────────────────────────────────────────────

export interface BoardColumnDto {
  key: string;
  name: string;
  is_done?: boolean;
  wip_limit?: number;
}

export type BoardProvider = "local" | "azure_devops" | "jira";
export type SyncDirection = "pull" | "two_way";

export interface BoardDto {
  id: number;
  name: string;
  description: string | null;
  key_prefix: string;
  lab_id: string | null;
  lab_name?: string | null;
  lab_status?: string | null;
  columns: BoardColumnDto[];
  agent_column: string | null;
  agent_done_column: string | null;
  agent_instruction: string | null;
  provider: BoardProvider;
  provider_config: Record<string, any>;
  has_secret: boolean;
  sync_direction: SyncDirection;
  auto_sync_minutes: number;
  last_sync_at: string | null;
  last_sync_error: string | null;
  ticket_counts?: Record<string, number>;
  ticket_total?: number;
  created_at: string;
  updated_at: string;
}

export type TicketAgentState = "idle" | "queued" | "running" | "done" | "failed";
export type TicketPriority = "low" | "normal" | "high" | "urgent";

export interface TicketDto {
  id: number;
  board_id: number;
  key: string;
  title: string;
  /** De opdracht, in Markdown. Geen werklogboek — dat zijn de comments. */
  description: string | null;
  /** Wanneer is het klaar? Markdown, meestal een lijstje. */
  acceptance_criteria: string | null;
  status: string;
  priority: TicketPriority;
  assignee: string | null;
  labels: string[];
  position: number;
  agent_state: TicketAgentState;
  agent_run_id: string | null;
  agent_thread_id: string | null;
  agent_last_error: string | null;
  external_provider: string | null;
  external_id: string | null;
  external_key: string | null;
  external_url: string | null;
  external_synced_at: string | null;
  dirty: boolean;
  created_at: string;
  updated_at: string;
  comments?: TicketCommentDto[];
}

export interface TicketCommentDto {
  id: number;
  ticket_id: number;
  kind: "comment" | "activity";
  author: string;
  body: string;
  external_id: string | null;
  pushed: boolean;
  /** Intern = blijft in LabX, gaat nooit naar Jira/DevOps. Alles van de agent
   *  is intern tot iemand het promoveert. */
  internal: boolean;
  created_at: string;
}

export interface ProviderFieldSpec {
  key: string;
  label: string;
  required: boolean;
  placeholder?: string;
  multiline?: boolean;
}

export interface ProviderSpec {
  key: BoardProvider;
  name: string;
  description: string;
  fields: ProviderFieldSpec[];
  secret_label: string | null;
  state_hint?: string;
  write_note?: string;
}

/** Een kolom zoals de BRON hem kent (Jira-bordkolom): een naam met de
 *  statussen die eronder vallen — bijna nooit precies één. */
export interface ExternalBoardColumn {
  name: string;
  states: string[];
}

export interface BoardSyncStats {
  board_id: number;
  provider: string;
  direction: SyncDirection;
  pushed: number;
  created_external: number;
  comments_pushed: number;
  pulled: number;
  created_local: number;
  updated_local: number;
  comments_pulled: number;
  skipped_dirty: number;
  /** Tickets die buiten de board-query vielen en toch zijn bijgewerkt. */
  reconciled?: number;
  /** Wat de automatische statusmapping veranderde (leeg = niets te doen). */
  mapping?: string[];
  /** Statussen uit de bron die op geen enkele kolom gemapt zijn — die tickets
   *  belanden in de eerste kolom. */
  unmapped_states?: string[];
  errors: string[];
}

export interface AgentRunStart {
  run_id: string;
  thread_id: string;
  ticket_id: number;
  ticket_key: string;
  status: string;
}
