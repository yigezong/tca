const statusElement = document.getElementById('server-status');
const form = document.getElementById('claim-form');
const result = document.getElementById('claim-result');

async function checkHealth() {
  try {
    const response = await fetch('/health', { cache: 'no-store' });
    if (!response.ok) throw new Error('Server unavailable');
    statusElement.classList.add('online');
    statusElement.querySelector('span:last-child').textContent = 'Server online';
  } catch {
    statusElement.classList.add('offline');
    statusElement.querySelector('span:last-child').textContent = 'Server unavailable';
  }
}

function element(tag, className, value) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (value !== undefined) node.textContent = value;
  return node;
}

function showMessage(message, isError = false) {
  result.className = `claim-result${isError ? ' error' : ''}`;
  result.replaceChildren(element('span', 'result-symbol', isError ? '!' : '↗'), element('p', '', message));
}

function renderClaim(data) {
  const claim = data.claim || {};
  result.className = 'claim-result loaded';
  const title = element('div', 'result-title');
  title.append(element('span', '', claim.claim_id || 'Claim'), element('span', 'state-chip', data.state || 'unknown'));
  const goal = element('p', 'result-goal', claim.goal || 'No goal recorded.');
  const meta = element('div', 'result-meta');
  const author = element('span', '', claim.author || 'Unknown');
  author.prepend(element('strong', '', 'Owner'));
  const branch = element('span', '', claim.work_branch || 'Not set');
  branch.prepend(element('strong', '', 'Work branch'));
  meta.append(author, branch);
  const children = [title, goal, meta];

  if (Array.isArray(claim.owned_symbols) && claim.owned_symbols.length) {
    const heading = element('p', 'result-decision', 'Owned symbols');
    const list = element('ul', 'result-list');
    claim.owned_symbols.forEach((symbol) => list.append(element('li', '', symbol)));
    children.push(heading, list);
  } else if (Array.isArray(claim.planned_paths) && claim.planned_paths.length) {
    const heading = element('p', 'result-decision', 'Planned paths');
    const list = element('ul', 'result-list');
    claim.planned_paths.forEach((path) => list.append(element('li', '', path)));
    children.push(heading, list);
  }
  if (data.decision?.reason) children.push(element('p', 'result-decision', `Decision: ${data.decision.reason}`));
  if (data.progress) children.push(element('p', 'result-decision', `Progress: ${data.progress}`));
  result.replaceChildren(...children);
}

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  const projectId = form.elements.project_id.value.trim();
  const claimId = form.elements.claim_id.value.trim();
  const token = form.elements.api_token.value.trim();
  if (!projectId || !claimId || !token) return;
  const button = form.querySelector('button');
  button.disabled = true;
  showMessage('Loading claim…');
  try {
    const url = `/api/claims/${encodeURIComponent(claimId)}?project_id=${encodeURIComponent(projectId)}`;
    const response = await fetch(url, { headers: { Authorization: `Bearer ${token}` }, cache: 'no-store' });
    if (response.status === 401) throw new Error('Invalid access token.');
    if (response.status === 404) throw new Error('Claim not found in this project.');
    if (!response.ok) throw new Error('Could not load this Claim. Please try again.');
    renderClaim(await response.json());
  } catch (error) {
    showMessage(error.message || 'Could not load this Claim.', true);
  } finally {
    button.disabled = false;
  }
});

checkHealth();
