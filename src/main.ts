import './styles.css';

import {
  ApiError,
  ApiProtocolError,
  ContractValidationError,
  FixtureRetrievalApi,
  MissingCredentialError,
  RetrievalApiClient,
  UnsupportedMockFixtureError,
  type RetrievalApi,
} from './api/index.ts';
import type {
  MCPFetchResult,
  PolicyState,
  RetrievalPolicy,
  SearchRequest,
  SearchResponse,
  SourceStatus,
  StatusResponse,
} from './api/generated/contracts.ts';
import {
  clonePolicy,
  createPolicyUpdate,
  createSearchRequest,
  diffPolicies,
  RESEARCH_LENSES,
  rankHits,
  upsertMetadataLine,
} from './ui/view-model.ts';

type ConnectionMode = 'fixture' | 'live';

function element<T extends HTMLElement>(id: string): T {
  const node = document.getElementById(id);
  if (!node) throw new Error(`Missing element #${id}`);
  return node as T;
}

function button(id: string): HTMLButtonElement {
  return element<HTMLButtonElement>(id);
}

function clear(node: HTMLElement): void {
  node.replaceChildren();
}

function textNode(tag: keyof HTMLElementTagNameMap, className: string, text: string): HTMLElement {
  const node = document.createElement(tag);
  node.className = className;
  node.textContent = text;
  return node;
}

function formatScore(value: number): string {
  return value === 0 ? '0' : value.toExponential(3);
}

function formatDate(value: string): string {
  if (!value) return '时间未知';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleDateString('zh-CN');
}

function describeError(error: unknown): string {
  if (error instanceof ApiError) {
    const labels: Record<number, string> = {
      401: '读凭据无效或已过期',
      403: '当前凭据没有管理权限',
      404: '目标资源不存在或已停用',
      409: '策略版本冲突，请重新载入后核对',
      413: '请求内容过大',
      422: '请求字段未通过上游校验',
    };
    return labels[error.status] ?? `${error.code}（HTTP ${error.status}）`;
  }
  if (error instanceof MissingCredentialError) return '缺少所需凭据，请在连接环境中补充 Token';
  if (error instanceof UnsupportedMockFixtureError) return `Fixture 限制：${error.message}`;
  if (error instanceof ApiProtocolError) return `接口契约不匹配：${error.message}`;
  if (error instanceof ContractValidationError) return `字段校验失败：${error.message}`;
  if (error instanceof Error) return error.message;
  return '发生未知错误';
}

class Dashboard {
  private api: RetrievalApi = new FixtureRetrievalApi();
  private mode: ConnectionMode = 'fixture';
  private sources: SourceStatus[] = [];
  private status: StatusResponse | undefined;
  private policyState: PolicyState | undefined;
  private policyDraft: RetrievalPolicy = {};
  private searchResponse: SearchResponse | undefined;
  private comparisonBaseline: SearchResponse | undefined;
  private lastSearchRequest: SearchRequest | undefined;
  private toastTimer: ReturnType<typeof setTimeout> | undefined;

  async start(): Promise<void> {
    this.renderResearchLenses();
    this.bindEvents();
    await this.refreshWorkspace();
  }

  private bindEvents(): void {
    element<HTMLSelectElement>('connection-mode').addEventListener('change', (event) => {
      const mode = (event.currentTarget as HTMLSelectElement).value as ConnectionMode;
      element('live-fields').hidden = mode !== 'live';
      button('example-policy').hidden = mode !== 'fixture';
    });

    element<HTMLFormElement>('connection-form').addEventListener('submit', (event) => {
      event.preventDefault();
      void this.connect();
    });
    element<HTMLFormElement>('policy-form').addEventListener('submit', (event) => {
      event.preventDefault();
      void this.savePolicy();
    });
    button('example-policy').addEventListener('click', () => this.useExamplePolicy());
    for (const inputId of ['recency-boost', 'half-life-days', 'default-limit']) {
      element<HTMLInputElement>(inputId).addEventListener('input', () => this.renderPolicyChanges());
    }
    button('reload-policy').addEventListener('click', () => void this.reloadPolicy());
    element<HTMLFormElement>('search-form').addEventListener('submit', (event) => {
      event.preventDefault();
      void this.search();
    });
    element('research-lenses').addEventListener('click', (event) => {
      const target = (event.target as HTMLElement).closest<HTMLButtonElement>('button[data-query]');
      if (!target) return;
      this.selectResearchLens(target.dataset.query ?? '', target);
    });
    button('fixture-query').addEventListener('click', () => this.selectResearchLens('芯片'));
    button('close-document').addEventListener('click', () => {
      element('document-panel').hidden = true;
    });
  }

  private renderResearchLenses(): void {
    const container = element('research-lenses');
    clear(container);
    for (const lens of RESEARCH_LENSES) {
      const lensButton = document.createElement('button');
      lensButton.type = 'button';
      lensButton.className = 'lens-button';
      lensButton.dataset.query = lens.query;
      lensButton.textContent = lens.label;
      lensButton.setAttribute('aria-pressed', 'false');
      container.append(lensButton);
    }
  }

  private selectResearchLens(query: string, selected?: HTMLButtonElement): void {
    element<HTMLInputElement>('search-query').value = query;
    for (const lensButton of element('research-lenses').querySelectorAll<HTMLButtonElement>('button')) {
      lensButton.setAttribute('aria-pressed', String(lensButton === selected));
    }
    this.showToast(
      this.mode === 'fixture' && query !== '芯片'
        ? '已填入研究主题；Fixture 仅支持“芯片”基线查询'
        : `已填入查询：${query}`,
      'success',
    );
  }

  private async connect(): Promise<void> {
    const mode = element<HTMLSelectElement>('connection-mode').value as ConnectionMode;
    this.mode = mode;

    if (mode === 'fixture') {
      this.api = new FixtureRetrievalApi();
    } else {
      const baseUrl = element<HTMLInputElement>('base-url').value.trim();
      const readToken = element<HTMLInputElement>('read-token').value;
      const adminToken = element<HTMLInputElement>('admin-token').value;
      if (!baseUrl || !readToken) {
        this.showToast('Live 模式需要 Base URL 和 Read Token', 'error');
        return;
      }
      this.api = new RetrievalApiClient({
        baseUrl,
        readToken,
        ...(adminToken ? { adminToken } : {}),
      });
    }

    this.searchResponse = undefined;
    this.comparisonBaseline = undefined;
    this.lastSearchRequest = undefined;
    await this.refreshWorkspace();
  }

  private async refreshWorkspace(): Promise<void> {
    this.setBusy(button('connect-button'), true, '载入中…');
    this.setConnectionState('正在连接', 'loading');
    try {
      const [status, sources, policy] = await Promise.all([
        this.api.getStatus(),
        this.api.getSources(),
        this.api.getPolicy(),
      ]);
      this.status = status;
      this.sources = sources.sources;
      this.policyState = policy;
      this.policyDraft = clonePolicy(policy.policy);
      this.renderWorkspace();
      this.setConnectionState(this.mode === 'fixture' ? 'Fixture 已就绪' : '服务已连接', 'ready');
      this.showToast(`已载入 Policy v${policy.version}`, 'success');
    } catch (error) {
      this.setConnectionState('连接失败', 'error');
      this.showToast(describeError(error), 'error');
    } finally {
      this.setBusy(button('connect-button'), false, '连接并载入');
    }
  }

  private renderWorkspace(): void {
    this.renderStatus();
    this.renderSources();
    this.renderPolicy();
    this.renderSourceFilters();
    this.renderResults();
  }

  private renderStatus(): void {
    if (!this.status) return;
    const values = [
      [String(this.status.documents), 'Documents'],
      [String(this.status.chunks), 'Chunks'],
      [String(this.status.sources), 'Sources'],
      [`v${this.status.policy_version}`, 'Policy'],
    ];
    const grid = element('status-grid');
    clear(grid);
    values.forEach(([value, label], index) => {
      const metric = document.createElement('div');
      metric.className = index === 3 ? 'metric metric--accent' : 'metric';
      metric.append(textNode('strong', '', value ?? '—'), textNode('span', '', label ?? ''));
      grid.append(metric);
    });
  }

  private renderSources(): void {
    const list = element('sources-list');
    clear(list);
    for (const source of this.sources) {
      const row = document.createElement('div');
      row.className = 'source-row';
      const statusDot = document.createElement('span');
      statusDot.className = source.enabled === false ? 'source-dot source-dot--off' : 'source-dot';
      statusDot.setAttribute('aria-label', source.enabled === false ? '已停用' : '已启用');
      const copy = document.createElement('div');
      copy.append(
        textNode('strong', '', source.name),
        textNode('span', '', `${source.id} · ${source.document_count} documents`),
      );
      row.append(statusDot, copy);
      list.append(row);
    }
  }

  private renderPolicy(): void {
    if (!this.policyState) return;
    element('policy-version').textContent = `v${this.policyState.version}`;
    element<HTMLInputElement>('recency-boost').value = String(this.policyDraft.recency_boost ?? 0.25);
    element<HTMLInputElement>('half-life-days').value = String(this.policyDraft.half_life_days ?? 90);
    element<HTMLInputElement>('default-limit').value = String(this.policyDraft.default_limit ?? 10);
    this.renderWeights();
    this.renderPolicyChanges();
  }

  private renderWeights(): void {
    const container = element('weight-fields');
    clear(container);
    for (const source of this.sources) {
      const label = document.createElement('label');
      label.className = 'weight-row';
      const copy = document.createElement('span');
      copy.append(textNode('strong', '', source.name), textNode('small', '', source.id));
      const input = document.createElement('input');
      input.type = 'number';
      input.min = '0';
      input.max = '10';
      input.step = '0.1';
      input.value = String(this.policyDraft.source_weights?.[source.id] ?? 1);
      input.setAttribute('aria-label', `${source.name} 来源权重`);
      input.addEventListener('input', () => {
        const sourceWeights = this.policyDraft.source_weights ?? {};
        sourceWeights[source.id] = Number(input.value);
        this.policyDraft.source_weights = sourceWeights;
        this.renderPolicyChanges();
      });
      label.append(copy, input);
      container.append(label);
    }
  }

  private renderPolicyChanges(): void {
    if (!this.policyState) return;
    const summary = element('policy-change-summary');
    const list = element('policy-change-list');
    const draft = this.collectPolicyDraft();
    const changes = diffPolicies(
      this.policyState.policy,
      draft,
      this.sources.map((source) => source.id),
    );
    clear(list);
    summary.hidden = changes.length === 0;

    const fieldLabels = {
      recency_boost: '新文档加成',
      half_life_days: '时间半衰期',
      default_limit: '默认结果数',
    } as const;
    for (const change of changes) {
      const item = document.createElement('li');
      const label = change.field === 'source_weight'
        ? `${this.sources.find((source) => source.id === change.sourceId)?.name ?? change.sourceId} 权重`
        : fieldLabels[change.field];
      item.textContent = `${label}：${change.before} → ${change.after}`;
      list.append(item);
    }
  }

  private renderSourceFilters(): void {
    const container = element('source-filters');
    clear(container);
    for (const source of this.sources) {
      const label = document.createElement('label');
      const input = document.createElement('input');
      input.type = 'checkbox';
      input.value = source.id;
      input.disabled = source.enabled === false;
      label.append(input, document.createTextNode(source.name));
      container.append(label);
    }
  }

  private useExamplePolicy(): void {
    if (this.mode !== 'fixture') return;
    this.policyDraft = {
      source_weights: { demo_research: 0.5, demo_filings: 3 },
      recency_boost: 0.25,
      half_life_days: 90,
      default_limit: 10,
    };
    this.renderPolicy();
    this.showToast('已填入官方 policy_update_request 样例', 'success');
  }

  private collectPolicyDraft(): RetrievalPolicy {
    const policy = clonePolicy(this.policyDraft);
    policy.recency_boost = Number(element<HTMLInputElement>('recency-boost').value);
    policy.half_life_days = Number(element<HTMLInputElement>('half-life-days').value);
    policy.default_limit = Number(element<HTMLInputElement>('default-limit').value);
    return policy;
  }

  private async savePolicy(): Promise<void> {
    if (!this.policyState) return;
    button('save-policy').disabled = true;
    element('policy-conflict').hidden = true;
    try {
      const draft = this.collectPolicyDraft();
      const update = createPolicyUpdate(this.policyState, draft);
      this.comparisonBaseline = this.searchResponse ? structuredClone(this.searchResponse) : undefined;
      this.policyState = await this.api.updatePolicy(update);
      this.policyDraft = clonePolicy(this.policyState.policy);
      this.renderPolicy();
      this.showToast(`Policy v${this.policyState.version} 已保存`, 'success');
      if (this.lastSearchRequest) await this.runSearch(this.lastSearchRequest);
      this.status = await this.api.getStatus();
      this.renderStatus();
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) element('policy-conflict').hidden = false;
      this.showToast(describeError(error), 'error');
    } finally {
      button('save-policy').disabled = false;
    }
  }

  private async reloadPolicy(): Promise<void> {
    try {
      this.policyState = await this.api.getPolicy();
      this.policyDraft = clonePolicy(this.policyState.policy);
      element('policy-conflict').hidden = true;
      this.renderPolicy();
      this.showToast(`已重新载入 Policy v${this.policyState.version}`, 'success');
    } catch (error) {
      this.showToast(describeError(error), 'error');
    }
  }

  private collectSearchRequest(): SearchRequest {
    const selectedSources = Array.from(
      element('source-filters').querySelectorAll<HTMLInputElement>('input:checked'),
    ).map((input) => input.value);
    const since = element<HTMLInputElement>('filter-since').value;
    const until = element<HTMLInputElement>('filter-until').value;
    return createSearchRequest({
      query: element<HTMLInputElement>('search-query').value,
      source_ids: selectedSources,
      since: since ? new Date(since).toISOString() : '',
      until: until ? new Date(until).toISOString() : '',
      metadataText: element<HTMLTextAreaElement>('filter-metadata').value,
      limit: Number(element<HTMLInputElement>('search-limit').value),
    });
  }

  private async search(): Promise<void> {
    try {
      const request = this.collectSearchRequest();
      this.lastSearchRequest = request;
      await this.runSearch(request);
    } catch (error) {
      this.showToast(describeError(error), 'error');
    }
  }

  private async runSearch(request: SearchRequest): Promise<void> {
    this.setBusy(button('search-button'), true, '检索中…');
    try {
      this.searchResponse = await this.api.search(request);
      this.renderResults();
      this.showToast(`检索完成 · Policy v${this.searchResponse.policy_version}`, 'success');
    } finally {
      this.setBusy(button('search-button'), false, '检索证据');
    }
  }

  private renderResults(): void {
    const list = element('results-list');
    if (!this.searchResponse) return;
    clear(list);
    const response = this.searchResponse;
    element('result-summary').textContent = `${response.total} results · ${response.elapsed_ms.toFixed(1)} ms · v${response.policy_version}`;

    for (const ranked of rankHits(response, this.comparisonBaseline)) {
      const card = document.createElement('article');
      card.className = 'result-card';

      const rank = textNode('div', 'result-rank', String(ranked.currentRank).padStart(2, '0'));
      const body = document.createElement('div');
      body.className = 'result-body';

      const heading = document.createElement('div');
      heading.className = 'result-heading';
      const titleBlock = document.createElement('div');
      titleBlock.append(
        textNode('p', 'result-kicker', `${ranked.hit.source_id} · ${formatDate(ranked.hit.published_at)}`),
        textNode('h3', '', ranked.hit.title),
      );
      heading.append(titleBlock);

      if (ranked.movement !== null) {
        const movement = document.createElement('span');
        const direction = ranked.movement > 0 ? 'up' : ranked.movement < 0 ? 'down' : 'same';
        movement.className = `movement movement--${direction}`;
        movement.textContent = ranked.movement > 0
          ? `↑ ${ranked.movement}`
          : ranked.movement < 0
            ? `↓ ${Math.abs(ranked.movement)}`
            : '— 0';
        movement.title = `上次排名 ${ranked.previousRank ?? '无'}，当前排名 ${ranked.currentRank}`;
        heading.append(movement);
      }

      const snippet = textNode('p', 'snippet', ranked.hit.snippet);
      const scores = document.createElement('div');
      scores.className = 'score-grid';
      const scoreItems: Array<[string, string]> = [
        ['最终分', formatScore(ranked.hit.score)],
        ['相关性', formatScore(ranked.hit.score_details.relevance)],
        ['来源权重', ranked.hit.score_details.source_weight.toFixed(2)],
        ['新鲜度', ranked.hit.score_details.freshness.toFixed(3)],
      ];
      for (const [label, value] of scoreItems) {
        const item = document.createElement('div');
        item.append(textNode('span', '', label), textNode('strong', '', value));
        scores.append(item);
      }

      const footer = document.createElement('div');
      footer.className = 'result-footer';
      footer.append(textNode('span', '', `引用位置 ${ranked.hit.citation.start}–${ranked.hit.citation.end}`));
      const fetchButton = document.createElement('button');
      fetchButton.type = 'button';
      fetchButton.className = 'text-button';
      fetchButton.textContent = '查看全文';
      fetchButton.addEventListener('click', () => void this.openDocument(ranked.hit.id));
      footer.append(fetchButton);

      body.append(heading, snippet, scores, footer);
      card.append(rank, body);
      list.append(card);
    }
  }

  private async openDocument(documentId: string): Promise<void> {
    try {
      const document = await this.api.getDocument(documentId);
      this.renderDocument(document);
      element('document-panel').hidden = false;
      element('document-panel').scrollIntoView({ behavior: 'smooth', block: 'start' });
    } catch (error) {
      this.showToast(describeError(error), 'error');
    }
  }

  private renderDocument(document: MCPFetchResult): void {
    const container = element('document-content');
    clear(container);
    const header = documentNode(document.title, document.url);
    const body = textNode('div', 'document-text', document.text);
    const metadataSuggestions = this.createMetadataSuggestions(document.metadata.attributes);
    const metadata = textNode('pre', 'metadata-block', JSON.stringify(document.metadata, null, 2));
    container.append(header, body, metadataSuggestions, metadata);
  }

  private createMetadataSuggestions(attributes: Record<string, string>): HTMLElement {
    const wrapper = document.createElement('div');
    wrapper.className = 'metadata-suggestions';
    wrapper.append(textNode('strong', '', '用于下一次检索'));
    const list = document.createElement('div');
    list.className = 'metadata-suggestion-list';

    const entries = Object.entries(attributes).filter(
      ([key, value]) => key && value && !/[=\r\n]/.test(key) && !/[\r\n]/.test(value),
    );
    if (entries.length === 0) {
      list.append(textNode('span', 'field-help', '该文档没有可复用的 attributes'));
    } else {
      for (const [key, value] of entries) {
        const suggestion = document.createElement('button');
        suggestion.type = 'button';
        suggestion.className = 'metadata-suggestion';
        suggestion.textContent = `${key}=${value}`;
        suggestion.addEventListener('click', () => this.useMetadataFilter(key, value));
        list.append(suggestion);
      }
    }
    wrapper.append(list);
    return wrapper;
  }

  private useMetadataFilter(key: string, value: string): void {
    try {
      const input = element<HTMLTextAreaElement>('filter-metadata');
      input.value = upsertMetadataLine(input.value, key, value);
      element<HTMLDetailsElement>('search-filters').open = true;
      input.focus();
      this.showToast(`已加入本次查询过滤：${key}=${value}`, 'success');
    } catch (error) {
      this.showToast(describeError(error), 'error');
    }
  }

  private setConnectionState(label: string, state: 'idle' | 'loading' | 'ready' | 'error'): void {
    const node = element('connection-state');
    node.className = `status-badge status-badge--${state}`;
    node.textContent = label;
  }

  private setBusy(target: HTMLButtonElement, busy: boolean, label: string): void {
    target.disabled = busy;
    target.textContent = label;
  }

  private showToast(message: string, type: 'success' | 'error'): void {
    const toast = element('toast');
    toast.className = `toast toast--${type}`;
    toast.textContent = message;
    toast.hidden = false;
    if (this.toastTimer) clearTimeout(this.toastTimer);
    this.toastTimer = setTimeout(() => {
      toast.hidden = true;
    }, 4_500);
  }
}

function documentNode(title: string, url: string): HTMLElement {
  const wrapper = document.createElement('div');
  wrapper.className = 'document-header';
  wrapper.append(textNode('h3', '', title));
  const link = document.createElement('a');
  link.href = url;
  link.target = '_blank';
  link.rel = 'noreferrer';
  link.textContent = '打开引用地址';
  wrapper.append(link);
  return wrapper;
}

const dashboard = new Dashboard();
void dashboard.start();
