const CONCEPT_LABELS = {
  definition: "definition",
  parameters: "parameters",
  return_values: "return values",
  scope: "scope",
  default_args: "default args",
};

const state = {
  history: [],       
  current: null,    
  selectedOption: null,
  submitting: false,
};

const teachingPanel = document.getElementById("teaching-panel");
const graphEl = document.getElementById("graph");
const masteryEl = document.getElementById("mastery");

function escapeHtml(str) {
  return str
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}


function renderExplanation(text) {
  if (!text) return "";
  const parts = text.split(/```(?:python)?\n?([\s\S]*?)```/g);
  let html = "";
  parts.forEach((part, i) => {
    if (i % 2 === 1) {
      html += `<pre><code>${escapeHtml(part.trim())}</code></pre>`;
    } else {
      const paras = part.split(/\n{2,}/).map(p => p.trim()).filter(Boolean);
      html += paras.map(p => `<p>${escapeHtml(p).replace(/\n/g, "<br>")}</p>`).join("");
    }
  });
  return html;
}

async function fetchTurn(studentAnswer) {
  const res = await fetch("/api/turn", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      history: state.history,
      student_answer: studentAnswer,
    }),
  });
  const data = await res.json();
  if (!res.ok) {
    throw new Error(data.message || "The tutor hit an error.");
  }
  return data;
}

function renderLoading() {
  teachingPanel.innerHTML = `<div class="loading">Thinking about the best next step</div>`;
}

function renderError(message) {
  teachingPanel.innerHTML = `
    <div class="error-box">
      Something went wrong talking to the tutor engine.<br>${escapeHtml(message)}
    </div>
    <div style="margin-top:16px;">
      <button class="restart-btn" onclick="location.reload()">Reload</button>
    </div>
  `;
}

function renderTurn(turn) {
  state.current = turn;
  state.selectedOption = null;

  const actionLabel = turn.action.replace("_", " ");
  const conceptLabel = CONCEPT_LABELS[turn.concept_tag] || turn.concept_tag;

  let questionBlockHtml = "";
  if (!turn.done) {
    if (turn.question_type === "mcq" && Array.isArray(turn.options)) {
      const optsHtml = turn.options.map((opt, idx) => `
        <button class="option-btn" data-idx="${idx}">${escapeHtml(opt)}</button>
      `).join("");
      questionBlockHtml = `
        <div class="question-block">
          <div class="question-text">${escapeHtml(turn.question)}</div>
          <div class="options">${optsHtml}</div>
          <button class="submit-btn" id="submit-btn" disabled>Answer</button>
        </div>
      `;
    } else {
      questionBlockHtml = `
        <div class="question-block">
          <div class="question-text">${escapeHtml(turn.question)}</div>
          <div class="short-answer-row">
            <textarea class="answer-input" id="answer-input" placeholder="Type your answer…"></textarea>
            <button class="submit-btn" id="submit-btn">Answer</button>
          </div>
        </div>
      `;
    }
  } else {
    questionBlockHtml = `
      <div class="done-banner">Session complete. Scroll the learning path to see the full route the tutor took you on.</div>
      <div style="margin-top:16px;">
        <button class="restart-btn" onclick="location.reload()">Start a new session</button>
      </div>
    `;
  }

  teachingPanel.innerHTML = `
    <div class="turn-meta">
      <span class="action-pill ${turn.action}">${escapeHtml(actionLabel)}</span>
      <span>· ${escapeHtml(conceptLabel)}</span>
    </div>
    ${turn.diagnosis && turn.diagnosis !== "start" ? `<div class="diagnosis">${escapeHtml(turn.diagnosis)}</div>` : ""}
    <div class="explanation">${renderExplanation(turn.explanation)}</div>
    ${questionBlockHtml}
  `;

  if (!turn.done) {
    if (turn.question_type === "mcq") {
      const buttons = teachingPanel.querySelectorAll(".option-btn");
      const submitBtn = document.getElementById("submit-btn");
      buttons.forEach(btn => {
        btn.addEventListener("click", () => {
          buttons.forEach(b => b.classList.remove("selected"));
          btn.classList.add("selected");
          state.selectedOption = btn.textContent;
          submitBtn.disabled = false;
        });
      });
      submitBtn.addEventListener("click", () => submitAnswer(state.selectedOption));
    } else {
      const input = document.getElementById("answer-input");
      const submitBtn = document.getElementById("submit-btn");
      submitBtn.addEventListener("click", () => {
        const val = input.value.trim();
        if (val) submitAnswer(val);
      });
      input.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
          submitBtn.click();
        }
      });
    }
  }

  renderGraph();
  renderMastery(turn.mastery_estimate);
}

function renderGraph() {
  if (state.history.length === 0) {
    graphEl.innerHTML = `<div class="graph-empty">No commits yet — this fills in as the tutor decides how to teach you.</div>`;
    return;
  }
  const commits = state.history.map((t, i) => {
    const isLast = i === state.history.length - 1;
    return `
      <div class="commit">
        <div class="rail">
          <div class="dot ${t.action}"></div>
          ${isLast ? "" : `<div class="line"></div>`}
        </div>
        <div class="body">
          <div class="concept">${CONCEPT_LABELS[t.concept_tag] || t.concept_tag}</div>
          <div class="action-label">${t.action.replace("_", " ")}</div>
        </div>
      </div>
    `;
  }).join("");
  graphEl.innerHTML = commits;
}

function renderMastery(estimate) {
  if (!estimate) { masteryEl.innerHTML = ""; return; }
  const rows = Object.keys(CONCEPT_LABELS).map(key => {
    const val = Math.max(0, Math.min(1, estimate[key] ?? 0));
    return `
      <div class="mastery-row">
        <div class="mastery-label"><span>${CONCEPT_LABELS[key]}</span><span>${Math.round(val * 100)}%</span></div>
        <div class="mastery-bar-track"><div class="mastery-bar-fill" style="width:${val * 100}%"></div></div>
      </div>
    `;
  }).join("");
  masteryEl.innerHTML = rows;
}

async function submitAnswer(answer) {
  if (state.submitting || !answer) return;
  state.submitting = true;
  renderLoading();
  try {
    const nextTurn = await fetchTurn(answer);
  
    if (state.history.length > 0) {
      state.history[state.history.length - 1].student_answer = answer;
    }
    state.history.push(nextTurn);
    renderTurn(nextTurn);
  } catch (err) {
    renderError(err.message);
  } finally {
    state.submitting = false;
  }
}

async function start() {
  renderLoading();
  try {
    const firstTurn = await fetchTurn(null);
    state.history.push(firstTurn);
    renderTurn(firstTurn);
  } catch (err) {
    renderError(err.message);
  }
}

start();
