export interface Cue {
    index: number;
    start_time: string;
    end_time: string;
    original_text: string;
    translated_text: string;
    needs_retranslation: boolean;
}
export interface VersionSummary {
    task_id: string;
    language: string;
    status: string;
    complete: boolean;
    revision: number;
    legacy: boolean;
    created_at: string;
}
export interface Source {
    source_id?: string;
    path?: string;
    source_url?: string;
    source_video?: string;
    language: string;
}
export interface Material {
    sources?: Source[];
    material_id: string;
    title: string;
    identity: string;
    versions: VersionSummary[];
    tasks: VersionSummary[];
    defaults: Record<string, string>;
    pinned: Record<string, string>;
}
export interface Issue {
    indices: number[];
    type: string;
    description: string;
    candidate_only?: boolean;
    metrics?: Record<string, unknown>;
}
export interface ReviewItem {
    item_id: string;
    check_id: string;
    indices: number[];
    issues: Issue[];
    state: string;
    outdated: boolean;
    context: Cue[];
    check_status: string;
    can_accept: boolean;
}
export interface Check {
    check_id: string;
    indices: number[];
    status: string;
    revision: number;
    outdated: boolean;
    details: Record<string, unknown>;
}
export interface History {
    id: string;
    kind: string;
    category?: string;
    at: string;
    revision?: number;
    indices?: number[];
    before?: Cue[];
    after?: Cue[];
    reason?: string;
    parent_task_id?: string;
}
export interface Artifact {
    artifact_id: string;
    kind: string;
    revision: number;
    status: string;
    path?: string;
    error?: string;
    outdated: boolean;
    partial: boolean;
    missing_indices: number[];
    missing?: boolean;
}
export interface Budget {
    baseline: number;
    limit: number;
    spent: number;
    reserved: number;
    available: number;
    unknown_calls: number;
}
export interface Operation {
    audio_seconds?: number;
    elapsed_seconds?: number;
    operation_id: string;
    kind: string;
    status: string;
    indices: number[];
    created_at: string;
    automatic: boolean;
    usage: number | null;
    candidate?: unknown;
    error?: string;
    budget?: Budget;
    derived_task_id?: string;
}
export interface Version extends VersionSummary {
    origin_operation?: { task_id: string; operation_id: string; bucket: string };
    check_state: 'checked' | 'incomplete' | 'unknown';
    material_id: string;
    source: string;
    source_video?: string;
    source_url?: string;
    source_language: string;
    entries: Cue[];
    checks: Check[];
    review_items: ReviewItem[];
    history: History[];
    artifacts: Artifact[];
    protected_indices: number[];
    parent_task_id?: string;
    operations?: Operation[];
    provenance: {
        indices: number[];
        task_id: string;
        model?: string;
        inherited?: boolean;
    }[];
    budget?: Budget;
    report: {
        first_pass_tokens?: number;
        summary_tokens?: number;
        summary_tokens_total?: number;
        token_usage?: {
            total_tokens: number;
            estimated_tokens?: number;
            unknown_usage_calls?: number;
        };
    };
}
export interface AdditionalRequest {
    action: 'check' | 'repair' | 'retranscribe';
    task_id: string;
    revision: number;
    indices: number[];
    item_id?: string;
    estimated_tokens: number;
    token_limit: number;
}
