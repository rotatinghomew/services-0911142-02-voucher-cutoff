const state = {context: null, current: null, analysis: null, request: null};
const element = identifier => document.getElementById(identifier);

async function request(path, body) {
  const response = await fetch(path, body === undefined ? {} : {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)
  });
  const value = await response.json();
  if (!response.ok) throw new Error(value.error || '请求失败');
  return value;
}

function message(value, error = false) {
  element('status').textContent = value;
  element('status').className = error ? 'error' : '';
}

async function guarded(action) {
  try { await action(); } catch (error) { message(error.message, true); }
}

function selected() {
  const selection = {};
  for (const control of state.context.product.options) selection[control[0]] = element('select-' + control[0]).value;
  return {revision: element('revision').value, selection};
}

function thresholdSelection() {
  return {
    year: element('select-year').value,
    window: Number(element('threshold-window').value),
    donut: Number(element('select-donut').value),
    followup: Number(element('select-followup').value),
    order: Number(element('select-order').value),
    kernel: element('select-kernel').value
  };
}

function thresholdRequest() {
  return {revision: element('revision').value, selection: thresholdSelection()};
}

function addSelect(container, identifier, labelText, choices, value) {
  const label = document.createElement('label');
  label.textContent = labelText;
  const select = document.createElement('select');
  select.id = identifier;
  for (const choice of choices) {
    const option = document.createElement('option');
    option.value = typeof choice === 'object' ? choice[0] : choice;
    option.textContent = typeof choice === 'object' ? choice[1] : choice;
    select.append(option);
  }
  select.value = String(value);
  label.append(select);
  container.append(label);
  return select;
}

function renderControls() {
  const revision = element('revision').value || state.context.default_revision;
  element('revision').replaceChildren();
  for (const dataset of state.context.datasets) {
    const option = document.createElement('option');
    option.value = dataset.revision;
    option.textContent = dataset.label + ' · ' + dataset.revision.slice(0, 9);
    element('revision').append(option);
  }
  element('revision').value = revision;
  if (!element('revision').value) element('revision').value = state.context.default_revision;
  if (!element('controls').children.length) {
    for (const [key, labelText, choices] of state.context.product.options) {
      addSelect(element('controls'), 'select-' + key, labelText, choices, state.context.product.selection[key]);
    }
  }
  if (!element('threshold-controls').children.length) {
    const defaults = state.context.product.threshold || {donut: 0, followup: 60, order: 1, kernel: 'triangular'};
    const windowLabel = document.createElement('label');
    windowLabel.textContent = '分析窗口（±年龄天数）';
    const windowInput = document.createElement('input');
    windowInput.id = 'threshold-window'; windowInput.type = 'number';
    windowInput.min = '5'; windowInput.max = '365'; windowInput.step = '5';
    windowInput.value = String((state.context.product.threshold && state.context.product.threshold.window) || state.context.product.selection.window || 90);
    windowLabel.append(windowInput);
    element('threshold-controls').append(windowLabel);
    addSelect(element('threshold-controls'), 'select-donut', '边界排除（±天）', [[0, '不排除'], [2, '±2天'], [5, '±5天'], [10, '±10天']], defaults.donut);
    addSelect(element('threshold-controls'), 'select-followup', '实际入托观察期（天）', [[60, '60天（政策）'], [70, '70天'], [90, '90天']], defaults.followup);
    addSelect(element('threshold-controls'), 'select-order', '局部多项式阶数', [[1, '线性'], [2, '二次']], defaults.order);
    addSelect(element('threshold-controls'), 'select-kernel', '核函数', [['triangular', '三角核'], ['uniform', '均匀核']], defaults.kernel);
  }
}

function displayValue(value) {
  if (value === null || value === undefined) return '未观测';
  if (typeof value === 'boolean') return value ? '是' : '否';
  if (typeof value === 'number') return Number.isInteger(value) ? String(value) : value.toFixed(4);
  return typeof value === 'object' ? JSON.stringify(value) : String(value);
}

const EXCLUSION_LABELS = {
  records_total: '年度申请总数',
  missing_age_history: '缺少年龄版本记录',
  age_not_visible_at_vintage: '截面时点尚无可用户龄',
  outside_window: '窗口外',
  donut_excluded: '边界排除范围内',
  not_recorded_at_cutoff: '资料截止后录入',
  immature_null_outcome: '结局未观测',
  immature_short_followup: '观察期不足',
  included: '纳入分析',
  later_correction: '截面后有订正',
  vintage_age_differs_from_latest: '截面年龄不同于最新值',
  cutoff_equality_count: '年龄恰等于门槛',
  eligible_n: '纳入的具资格数',
  ineligible_n: '纳入的不具资格数',
  voucher_users: '纳入的核销数',
  corrected_before_vintage: '截面前已订正'
};

function formatEstimate(value) {
  if (value === null || value === undefined) return '不报告';
  return (value >= 0 ? '+' : '') + value.toFixed(3);
}

function intervalText(interval) {
  if (!interval || interval[0] === null || interval[1] === null) return '区间不可用';
  return '[' + interval[0].toFixed(3) + ', ' + interval[1].toFixed(3) + ']';
}

function drawChart(series) {
  const canvas = element('chart');
  const context = canvas.getContext('2d');
  context.clearRect(0, 0, canvas.width, canvas.height);
  const values = series.filter(point => typeof point.y === 'number' && Number.isFinite(point.y));
  context.font = '12px sans-serif';
  if (!values.length) { context.fillText('该范围没有可定量的描述值', 30, 50); return; }
  const low = Math.min(0, ...values.map(point => point.y));
  const high = Math.max(1, ...values.map(point => point.y));
  const scale = value => 250 - (value - low) / (high - low || 1) * 210;
  context.strokeStyle = '#90a5ad';
  context.beginPath(); context.moveTo(45, 20); context.lineTo(45, 250); context.lineTo(940, 250); context.stroke();
  context.fillStyle = '#0a7168';
  const scatter = values.every(point => typeof point.x === 'number');
  const minimumX = scatter ? Math.min(...values.map(point => point.x)) : 0;
  const maximumX = scatter ? Math.max(...values.map(point => point.x)) : 1;
  values.forEach((point, index) => {
    const position = scatter ? 60 + (point.x - minimumX) / (maximumX - minimumX || 1) * 850 : 55 + index * 875 / values.length;
    if (scatter) {
      context.beginPath(); context.arc(position, scale(point.y), 3.5, 0, Math.PI * 2); context.fill();
    } else {
      context.fillRect(position, Math.min(scale(point.y), scale(0)), Math.max(3, 800 / values.length), Math.max(1, Math.abs(scale(point.y) - scale(0))));
    }
    if (values.length < 12) context.fillText(point.label, position, 275);
  });
  context.fillStyle = '#29434e';
  context.fillText(high.toFixed(2), 0, 45); context.fillText(low.toFixed(2), 0, 250);
  context.fillText(scatter ? '所选资料的数值坐标（仅为原始描述）' : '按表中顺序展示描述值；缺测不绘制为零', 50, 20);
}

function drawThresholdBins(bins) {
  const canvas = element('threshold-bins');
  const context = canvas.getContext('2d');
  context.clearRect(0, 0, canvas.width, canvas.height);
  context.font = '12px sans-serif';
  if (!bins || !bins.length) { context.fillText('窗口内没有可展示的分布', 30, 50); return; }
  const counts = bins.map(item => item.n);
  const high = Math.max(1, ...counts);
  const span = Math.max(...bins.map(item => item.right), Math.abs(Math.min(...bins.map(item => item.left))));
  const scaleX = value => 55 + (value + span) / (2 * span) * 860;
  const scaleY = value => 245 - value / high * 195;
  context.strokeStyle = '#90a5ad';
  context.beginPath(); context.moveTo(55, 20); context.lineTo(55, 245); context.lineTo(920, 245); context.stroke();
  const zero = scaleX(0);
  context.strokeStyle = '#b85b2b'; context.setLineDash([5, 4]);
  context.beginPath(); context.moveTo(zero, 20); context.lineTo(zero, 245); context.stroke();
  context.setLineDash([]);
  for (const item of bins) {
    const left = scaleX(item.left); const right = scaleX(item.right);
    context.fillStyle = item.right <= 0 ? '#3e8f78' : item.left >= 0 ? '#7a6ab5' : '#89979e';
    context.fillRect(left + 1, scaleY(item.n), Math.max(1, right - left - 2), 245 - scaleY(item.n));
  }
  context.fillStyle = '#29434e';
  context.fillText('每格登记数（左侧绿色 / 右侧紫色）；虚线为政策门槛', 60, 16);
  context.fillText('门槛左侧 →', 60, 270); context.fillText('← 门槛右侧', 800, 270);
  context.fillText(String(high), 8, 52); context.fillText('0', 35, 245);
}

function renderTable(container, fields, rows, labels) {
  container.replaceChildren();
  const header = document.createElement('tr');
  for (const field of fields) {
    const cell = document.createElement('th');
    cell.textContent = (labels && labels[field]) || field;
    header.append(cell);
  }
  const head = document.createElement('thead'); head.append(header); container.append(head);
  const body = document.createElement('tbody');
  for (const row of rows) {
    const line = document.createElement('tr');
    for (const field of fields) {
      const cell = document.createElement('td');
      cell.textContent = displayValue(row[field]);
      line.append(cell);
    }
    body.append(line);
  }
  container.append(body);
}

function card(label, value, extra) {
  const article = document.createElement('article');
  const title = document.createElement('span'); title.textContent = label;
  const amount = document.createElement('strong'); amount.textContent = value;
  article.append(title, amount);
  if (extra) { const note = document.createElement('small'); note.textContent = extra; article.append(note); }
  return article;
}

function renderBrowse(result, heading) {
  state.current = result;
  state.analysis = 'description';
  element('summary').replaceChildren();
  for (const [label, value] of Object.entries(result.view.summary)) element('summary').append(card(label, displayValue(value)));
  element('notes').replaceChildren();
  for (const note of result.view.notes) {
    const paragraph = document.createElement('p'); paragraph.textContent = note; element('notes').append(paragraph);
  }
  const rows = result.view.rows;
  const fields = rows.length ? Object.keys(rows[0]) : [];
  element('columns').replaceChildren(); element('rows').replaceChildren();
  const header = document.createElement('tr');
  for (const field of fields) { const cell = document.createElement('th'); cell.textContent = field; header.append(cell); }
  element('columns').append(header);
  for (const row of rows) {
    const line = document.createElement('tr'); line.tabIndex = 0;
    for (const field of fields) { const cell = document.createElement('td'); cell.textContent = displayValue(row[field]); line.append(cell); }
    line.onclick = () => { element('details').textContent = JSON.stringify(row, null, 2); };
    line.onkeydown = event => { if (event.key === 'Enter') line.click(); };
    element('rows').append(line);
  }
  drawChart(result.view.series);
  element('details').textContent = JSON.stringify({revision: result.revision, selection: result.selection,
    history: result.view.history || null}, null, 2);
  element('threshold-panel').hidden = true;
  element('browse-panel').hidden = false;
  message((heading || '已更新描述') + ' · 数据 ' + result.revision.slice(0, 12));
}

function showSection(identifier, visible) {
  element(identifier).hidden = !visible;
}

function renderThreshold(container, heading) {
  state.current = container;
  state.analysis = 'threshold';
  state.request = {revision: container.revision, selection: thresholdSelection()};
  const result = container.threshold;
  element('summary').replaceChildren();
  element('summary').append(card('有效申请', result.summary.included_n, '窗口内 ' + result.summary.window_records));
  element('summary').append(card('左侧样本', result.summary.left_n, '生效规则侧'));
  element('summary').append(card('右侧样本', result.summary.right_n, '不具资格侧'));
  element('summary').append(card('政策门槛', result.summary.cutoff_days, '年龄天数'));
  element('summary').append(card('资料截面', '第' + result.summary.vintage + '天', '决策时点'));
  element('threshold-panel').hidden = false;
  element('browse-panel').hidden = true;
  for (const identifier of ['threshold-estimates-section', 'threshold-support-section', 'threshold-diagnostic-section',
    'threshold-descriptive-section', 'threshold-covariate-section', 'threshold-distribution-section',
    'threshold-exclusion-section']) showSection(identifier, true);
  element('comparison-panel').hidden = true;
  element('threshold-status').textContent = result.status_label;
  element('threshold-status').className = 'banner status-' + result.status;
  element('threshold-status').setAttribute('data-identified', String(Boolean(result.identified)));
  element('threshold-rule').textContent = result.summary.rule.label + '；门槛落在 ' +
    (result.summary.rule.shift > 0 ? '门槛-1与门槛之间' : '门槛与门槛+1之间') +
    '；资料截面第 ' + result.summary.vintage + ' 天，观察期 ≥' + result.summary.followup + ' 天，前后各 ' +
    result.summary.window + ' 天窗口' + (result.summary.donut ? '（排除±' + result.summary.donut + '天）' : '') + '。';
  element('threshold-conclusion').textContent = result.conclusion || '';
  element('threshold-scope').textContent = result.scope || '';
  element('threshold-notes').replaceChildren();
  for (const note of (result.notes || []).concat(result.warnings || [])) {
    const paragraph = document.createElement('p'); paragraph.textContent = note; element('threshold-notes').append(paragraph);
  }
  const estimates = result.estimates || [];
  const body = element('threshold-estimates').querySelector('tbody');
  body.replaceChildren();
  for (const item of estimates) {
    const line = document.createElement('tr');
    const label = document.createElement('th'); label.textContent = item.label; line.append(label);
    const estimate = document.createElement('td'); estimate.textContent = formatEstimate(item.estimate); line.append(estimate);
    const interval = document.createElement('td'); interval.textContent = intervalText(item.interval); line.append(interval);
    const n = document.createElement('td'); n.textContent = displayValue(item.n); line.append(n);
    const direction = document.createElement('td');
    direction.textContent = item.estimate === null ? '不报告' :
      item.direction === 'positive' ? '区间在0以上' : item.direction === 'negative' ? '区间在0以下' : '方向未定';
    line.append(direction);
    const note = document.createElement('td'); note.textContent = item.note; line.append(note);
    body.append(line);
  }
  const support = result.support || {};
  element('threshold-support').replaceChildren();
  const supportItems = [
    ['有效样本（等价核）', displayValue(support.effective_n)],
    ['左侧有效样本', displayValue(support.effective_left_n)],
    ['右侧有效样本', displayValue(support.effective_right_n)],
    ['窗口记录', displayValue(result.summary.window_records)],
    ['核销/未核销', displayValue(support.voucher_n) + ' / ' + displayValue(support.nonvoucher_n)],
    ['在园结局为1', displayValue(support.outcome_ones)],
    ['不同的年龄取值', displayValue(support.distinct_x)]
  ];
  for (const [label, value] of supportItems) element('threshold-support').append(card(label, value));
  const diagnostics = result.diagnostics || {};
  const first = diagnostics.first_stage || {};
  const density = diagnostics.density || {};
  const maturity = diagnostics.maturity || {};
  element('threshold-diagnostics').replaceChildren();
  if (diagnostics.first_stage) {
    const row = document.createElement('div');
    row.textContent = '核销跃迁：' + formatEstimate(first.jump) + '，区间 ' + intervalText(first.interval) +
      '，F=' + displayValue(first.f_statistic) + (first.weak ? '（工具偏弱）' : '');
    element('threshold-diagnostics').append(row);
  }
  if (density.available) {
    const row = document.createElement('div');
    row.textContent = '登记密度连续性：p=' + displayValue(density.p_value) + '，统计量 ' + displayValue(density.statistic) +
      '，过散度 ' + displayValue(density.overdispersion);
    element('threshold-diagnostics').append(row);
  } else if (density.reason) {
    const row = document.createElement('div'); row.textContent = '登记密度：' + density.reason;
    element('threshold-diagnostics').append(row);
  }
  if (maturity && maturity.n) {
    const row = document.createElement('div');
    row.textContent = '随访成熟：左侧 ' + displayValue(maturity.left_share) + '，右侧 ' + displayValue(maturity.right_share) +
      '，跃迁 ' + formatEstimate(maturity.jump) + '，p=' + displayValue(maturity.p_value);
    element('threshold-diagnostics').append(row);
  }
  if (result.covariate_sample_n) {
    const row = document.createElement('div');
    row.textContent = '协变量检查用窗口内全部 ' + result.covariate_sample_n + ' 条记录（含结局未成熟），不因结局筛选而改变风险集合。';
    element('threshold-diagnostics').append(row);
  }
  if (result.methods) {
    const row = document.createElement('div');
    row.textContent = '方法：' + result.methods.estimator + '；' + result.methods.interval + '；' + result.methods.first_stage_rule + '。';
    element('threshold-diagnostics').append(row);
  }
  const vintage = diagnostics.vintage || {};
  if (vintage.decision_vintage !== undefined) {
    const row = document.createElement('div');
    row.textContent = '资料截面第 ' + vintage.decision_vintage + ' 天：之后订正 ' + (vintage.records_with_later_correction || 0) +
      ' 条；若改用最新年龄，' + (vintage.eligibility_would_change_with_latest || 0) + ' 条资格会改变（本次未采用最新字段）。';
    element('threshold-diagnostics').append(row);
  }
  element('threshold-descriptive').replaceChildren();
  const descriptive = result.descriptive || {};
  if (result.naive && result.naive.difference !== null && result.naive.difference !== undefined) {
    const row = document.createElement('div');
    row.textContent = '不受年龄门槛约束的直接比较（仅作反面参照）：核销家庭在园 ' + displayValue(result.naive.voucher_outcome) +
      '（' + displayValue(result.naive.voucher_users) + ' 户） vs 未核销 ' + displayValue(result.naive.nonvoucher_outcome) +
      '（' + displayValue(result.naive.voucher_nonusers) + ' 户），差 ' + formatEstimate(result.naive.difference) +
      '；' + (result.naive_note || '');
    element('threshold-descriptive').append(row);
  }
  if (descriptive.left) {
    for (const key of ['left', 'right']) {
      const side = descriptive[key];
      const row = document.createElement('div');
      row.textContent = (key === 'left' ? '门槛左（具资格）' : '门槛右（不具资格）') + '：窗口内登记 ' + displayValue(side.n) +
        '（成熟 ' + displayValue(side.mature_n) + '），核销比例 ' + displayValue(side.voucher_share) +
        '，成熟随访中在园比例 ' + displayValue(side.visit_share) + '，年龄中位 ' + displayValue(side.median_age);
      element('threshold-descriptive').append(row);
    }
    const note = document.createElement('div'); note.textContent = descriptive.note;
    element('threshold-descriptive').append(note);
  }
  element('threshold-covariates-heading').textContent = '门槛两侧协变量';
  element('threshold-distribution-heading').textContent = '门槛附近登记分布';
  element('threshold-covariates').closest('section').querySelector('h2').textContent =
    '门槛两侧协变量（窗口内 ' + displayValue(result.covariate_sample_n) + ' 条，含结局未成熟）';
  renderTable(element('threshold-covariates'), ['label', 'jump', 'p_value', 'constant'],
    result.covariates || [], {label: '门槛前协变量', jump: '门槛处跃迁', p_value: 'p值', constant: '无变异'});
  renderTable(element('threshold-exclusions'), ['key', 'value'], Object.entries(result.exclusions || {})
    .map(([key, value]) => ({key: EXCLUSION_LABELS[key] || key, value})), {key: '排除或计数项', value: '数量'});
  element('threshold-histogram').hidden = false;
  renderTable(element('threshold-histogram'), ['centre', 'n', 'voucher', 'mature', 'visits'],
    result.histograms || [], {centre: '年龄距门槛', n: '登记数', voucher: '核销数', mature: '成熟数', visits: '在园数'});
  drawThresholdBins(result.bins || []);
  element('comparison-panel').hidden = true;
  element('details').textContent = JSON.stringify({revision: container.revision, selection: container.selection,
    threshold: {status: result.status, summary: result.summary, support: result.support}}, null, 2);
  message((heading || '已更新门槛分析') + ' · 数据 ' + container.revision.slice(0, 12));
}

function renderComparison(container, heading) {
  state.current = container;
  state.analysis = 'comparison';
  state.request = {revision: container.revision, selection: thresholdSelection()};
  element('summary').replaceChildren();
  const scenarios = container.comparison.scenarios || [];
  element('summary').append(card('对照规格', scenarios.length, '同一年度与资料版本'));
  element('summary').append(card('可解释规格', scenarios.filter(item => item.status === 'identified').length, '识别检查通过'));
  element('summary').append(card('不报告效应的规格', scenarios.filter(item => item.status !== 'identified').length, '含弱工具或识别失败'));
  element('threshold-panel').hidden = false;
  element('browse-panel').hidden = true;
  element('threshold-status').textContent = '分析选择对照';
  element('threshold-status').className = 'banner';
  element('threshold-status').removeAttribute('data-identified');
  element('threshold-rule').textContent = '同一批申请资料，只改变分析窗口、边界排除、随访要求、阶数与核函数。';
  element('threshold-conclusion').textContent = '各规格的状态与区间不一致之处，就是结论依赖的假设。';
  element('threshold-scope').textContent = '';
  element('threshold-notes').replaceChildren();
  const note = document.createElement('p');
  note.textContent = '结论是否稳健，要看不同合理规格下区间与状态是否一致；不要把单一规格的比值当作全市平均效果。';
  element('threshold-notes').append(note);
  for (const identifier of ['threshold-estimates-section', 'threshold-support-section', 'threshold-distribution-section']) {
    showSection(identifier, false);
  }
  element('threshold-diagnostic-section').hidden = false;
  element('threshold-diagnostic-section').querySelector('h2').textContent = '怎么读这张表';
  element('threshold-descriptive-section').hidden = false;
  element('threshold-descriptive-section').querySelector('h2').textContent = '对照口径';
  element('threshold-diagnostics').replaceChildren();
  const shown = document.createElement('div');
  shown.textContent = '“识别条件通过”表示该规格的密度、协变量和随访检查都通过；其余状态表示该规格不应用于补贴效应解释。' +
    '“未观测”指该规格没有输出对应估计，而不是估计为零。';
  element('threshold-diagnostics').append(shown);
  element('threshold-descriptive').replaceChildren();
  const detail = document.createElement('div');
  const passed = scenarios.filter(item => item.status === 'identified');
  detail.textContent = passed.length
    ? '识别通过的规格共 ' + passed.length + ' 个；请对照它们的区间是否重叠、方向是否一致。'
    : '没有规格通过识别检查，本批资料只能用于描述与流程核对。';
  element('threshold-descriptive').append(detail);
  element('threshold-covariate-section').hidden = true;
  element('threshold-exclusion-section').hidden = true;
  renderTable(element('comparison-table'), ['name', 'status_label', 'n', 'itt', 'first_stage', 'late', 'late_interval'],
    scenarios.map(item => ({...item, late_interval: item.late_interval ? intervalText(item.late_interval) : '区间不可用'})),
    {name: '规格', status_label: '状态', n: '有效样本', itt: 'ITT', first_stage: '核销跃迁', late: 'LATE', late_interval: 'LATE区间'});
  element('comparison-panel').querySelector('h2').textContent = '各规格结果';
  element('comparison-panel').hidden = false;
  element('comparison-actions').replaceChildren();
  for (const item of scenarios) {
    const button = document.createElement('button');
    button.textContent = '按此规格运行：' + item.name;
    button.onclick = () => guarded(async () => {
      element('threshold-window').value = String(item.selection.window);
      element('select-donut').value = String(item.selection.donut);
      element('select-followup').value = String(item.selection.followup);
      element('select-order').value = String(item.selection.order);
      element('select-kernel').value = item.selection.kernel;
      renderThreshold(await request('/api/threshold', thresholdRequest()), '已按“' + item.name + '”重算');
    });
    element('comparison-actions').append(button);
  }
  element('details').textContent = JSON.stringify({revision: container.revision, selection: container.selection,
    comparison: scenarios}, null, 2);
  message((heading || '已更新分析选择对照') + ' · 数据 ' + container.revision.slice(0, 12));
}

function renderResult(result, heading) {
  if (result.analysis === 'threshold') return renderThreshold(result, heading);
  if (result.analysis === 'comparison') return renderComparison(result, heading);
  return renderBrowse(result, heading);
}

async function refresh() {
  state.context = await request('/api/context');
  renderControls();
  element('archive').replaceChildren();
  for (const notebook of state.context.notebooks) {
    const button = document.createElement('button'); button.textContent = notebook.title + ' #' + notebook.id;
    button.onclick = () => guarded(async () => {
      const saved = await request('/api/notebooks/' + notebook.id);
      element('revision').value = saved.revision;
      for (const [key, value] of Object.entries(saved.selection)) {
        const control = element(key === 'year' ? 'select-year' : 'threshold-' + key);
        if (control) control.value = String(value);
      }
      state.request = {revision: saved.revision, selection: saved.selection};
      const heading = saved.result.analysis === 'threshold' || saved.result.analysis === 'comparison'
        ? '已读取保存的分析，不随新数据变化' : '已读取保存的描述，不随新数据变化';
      renderResult(saved.result, heading);
    });
    element('archive').append(button);
  }
}

element('inspect').onclick = () => guarded(async () => renderBrowse(await request('/api/explore', selected())));
element('threshold').onclick = () => guarded(async () => {
  const sent = thresholdRequest();
  const received = await request('/api/threshold', sent);
  renderThreshold(received, '已更新门槛分析');
  state.request = sent;
});
element('compare').onclick = () => guarded(async () => {
  const sent = thresholdRequest();
  const received = await request('/api/compare', sent);
  renderComparison(received, '已更新分析选择对照');
  state.request = sent;
});
element('save').onclick = () => guarded(async () => {
  const saved = await request('/api/notebooks', {...selected(), title: element('title').value});
  await refresh(); renderResult(saved.result, '已保存记录 #' + saved.id);
});
element('save-threshold').onclick = () => guarded(async () => {
  if (!state.current || (state.analysis !== 'threshold' && state.analysis !== 'comparison')) throw new Error('请先运行门槛分析或对照');
  const latest = thresholdSelection();
  const shown = state.request ? state.request.selection : {};
  const same = Object.keys(latest).every(key => String(latest[key]) === String(shown[key]));
  if (!same || element('revision').value !== state.request.revision) throw new Error('设置已改动，请先重新运行门槛分析再保存');
  const saved = await request('/api/notebooks', {...state.request, analysis: state.analysis, title: element('title').value});
  await refresh(); renderResult(saved.result, '已保存分析 #' + saved.id);
});
element('raw').onclick = () => guarded(async () => {
  const revision = state.current ? state.current.revision : element('revision').value;
  element('details').textContent = JSON.stringify(await request('/api/data?revision=' + encodeURIComponent(revision)), null, 2);
});
element('upload').onchange = () => guarded(async () => {
  const file = element('upload').files[0]; if (!file) return;
  const data = JSON.parse(await file.text());
  const uploaded = await request('/api/datasets', {label: file.name, data});
  await refresh(); element('revision').value = uploaded.revision;
  renderBrowse(await request('/api/explore', selected()), '已导入独立数据版本');
});
element('export').onclick = () => guarded(async () => {
  if (!state.current) throw new Error('请先查看一个范围');
  const data = await request('/api/data?revision=' + encodeURIComponent(state.current.revision));
  const blob = new Blob([JSON.stringify({analysis: state.analysis, description: state.current, data}, null, 2)],
    {type: 'application/json'});
  const url = URL.createObjectURL(blob); const link = document.createElement('a');
  link.href = url; link.download = 'analysis-' + state.current.revision.slice(0, 12) + '.json'; link.click();
  URL.revokeObjectURL(url);
});
guarded(async () => { await refresh(); renderBrowse(await request('/api/explore', selected())); });
