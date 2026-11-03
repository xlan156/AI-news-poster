import { invoke } from '@tauri-apps/api/core';

export type Article = {id:string; title:string; summary:string; chinese_summary?:string; importance_score?:number; source:string; organization:string; category:string; canonical_url:string; published_at:string; first_seen_at:string; status:string; read_at:string|null};
export type Feed = {name:string; url:string; enabled:boolean; organization:string; type:string; category:string};
export type Config = {sources:Feed[]; refresh_minutes:number; max_notifications_per_cycle:number; notifications_enabled:boolean; notifications:{categories:string[]; exclude_keywords:string[]}; llm:{mode:string; provider:string; model:string; has_api_key:boolean; max_articles_per_day:number; max_requests_per_day:number; monthly_budget_usd:number|null}; news_filter:{min_importance_score:number; min_relevance_score:number; language:string}; preferences:{theme:string; language:string; start_minimized:boolean; minimize_to_tray:boolean; quiet_start:string; quiet_end:string; daily_digest:boolean; start_at_login:boolean}};
export type Status = {monitoring:boolean; last_refresh:string|null; next_refresh:string|null; last_result:{feeds_ok:number;feeds_failed:number;articles_added:number;notified:number}|null; article_count:number; new_count:number; source_status:Record<string,{state:string;last_attempt:string;last_success:string|null;last_error:string}>; data_dir:string; usage:{requests_today:number;articles_today:number;estimated_cost_month_usd:number}};
export type State = {status:Status; config:Config; articles:Article[]};
export type NewsResponse = {items:Article[]; total:number; organizations:string[]};
export async function command<T>(method:string, params:Record<string,unknown>={}):Promise<T> {
  return invoke<T>('backend_command', {method, params});
}
