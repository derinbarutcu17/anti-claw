document.addEventListener('DOMContentLoaded', () => {
    const chatBox = document.getElementById('chat-box');
    const form = document.getElementById('chat-form');
    const input = document.getElementById('prompt-input');
    const killBtn = document.getElementById('kill-btn');
    const modelSelect = document.getElementById('model-select');

    // Status Elements
    const proxyStatus = document.getElementById('proxy-status');
    const proxyDot = document.getElementById('proxy-dot');
    const daemonStatus = document.getElementById('daemon-status');
    const daemonDot = document.getElementById('daemon-dot');
    const activeTasks = document.getElementById('active-tasks');
    const memoriesCount = document.getElementById('memories-count');

    let currentTaskDiv = null;

    // Fetch initial status and models
    fetchStatus();
    fetchModels();
    setInterval(fetchStatus, 5000); // Poll every 5s

    // Setup SSE
    const evtSource = new EventSource('/api/stream');

    evtSource.onmessage = (event) => {
        const payload = JSON.parse(event.data);
        handleStreamEvent(payload);
    };

    evtSource.onerror = () => {
        console.error("SSE Connection lost. Reconnecting...");
        daemonStatus.textContent = "Reconnecting...";
        daemonDot.className = 'dot red';
    };

    form.addEventListener('submit', async (e) => {
        e.preventDefault();
        const prompt = input.value.trim();
        if (!prompt) return;

        appendMessage('user', prompt);
        input.value = '';

        try {
            const res = await fetch('/api/chat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ prompt })
            });
            const data = await res.json();

            if (!res.ok) {
                appendMessage('error', data.error || 'Failed to start task');
            }
        } catch (err) {
            appendMessage('error', err.message);
        }
    });

    killBtn.addEventListener('click', async () => {
        try {
            await fetch('/api/kill', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ task_id: 'web_dashboard' }) });
        } catch (e) { console.error('Kill failed', e); }
    });

    modelSelect.addEventListener('change', async (e) => {
        const model = e.target.value;
        if (!model) return;

        try {
            await fetch('/api/model', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ model })
            });
            appendMessage('system', `Model switched to: ${model}`);
            fetchStatus();
        } catch (err) {
            console.error('Failed to switch model', err);
        }
    });

    async function fetchStatus() {
        try {
            const res = await fetch('/api/status');
            const data = await res.json();

            daemonStatus.textContent = data.daemon_status;
            daemonDot.className = 'dot green pulse';

            activeTasks.textContent = data.active_tasks;
            memoriesCount.textContent = data.memories_indexed;

            if (data.proxy_online) {
                proxyStatus.textContent = "Online";
                proxyDot.className = 'dot green';
            } else {
                proxyStatus.textContent = "Offline";
                proxyDot.className = 'dot red pulse';
            }

            if (modelSelect.value !== data.active_model && Array.from(modelSelect.options).some(o => o.value === data.active_model)) {
                modelSelect.value = data.active_model;
            }
        } catch (err) {
            daemonStatus.textContent = "Offline";
            daemonDot.className = 'dot red';
        }
    }

    async function fetchModels() {
        try {
            const res = await fetch('/api/models');
            const { data } = await res.json();

            modelSelect.innerHTML = '';
            data.forEach(m => {
                const opt = document.createElement('option');
                opt.value = m.id;
                opt.textContent = m.description || m.id;
                modelSelect.appendChild(opt);
            });
            fetchStatus(); // trigger select update
        } catch (err) {
            modelSelect.innerHTML = '<option value="">Error loading</option>';
        }
    }

    function handleStreamEvent({ type, data }) {
        if (type === 'system') {
            appendMessage('system', data);
            currentTaskDiv = null;
        } else if (type === 'tool_start') {
            ensureCurrentTaskDiv();
            const toolDiv = document.createElement('div');
            toolDiv.className = 'tool-execution';
            toolDiv.textContent = `> Running ${data.name}(${JSON.stringify(data.params)})`;
            currentTaskDiv.appendChild(toolDiv);
            scrollToBottom();
        } else if (type === 'tool_end') {
            // Optional: indicate tool finished
        } else if (type === 'text') {
            ensureCurrentTaskDiv();
            const chunkSpan = document.createElement('span');
            chunkSpan.className = 'stream-chunk';
            chunkSpan.textContent = data;
            currentTaskDiv.appendChild(chunkSpan);
            scrollToBottom();
        } else if (type === 'final') {
            // Overwrite the streamed text with perfectly formatted markdown once complete
            ensureCurrentTaskDiv();
            if (currentTaskDiv) {
                currentTaskDiv.innerHTML = marked.parse(data);
                currentTaskDiv = null;
                scrollToBottom();
            }
        } else if (type === 'error') {
            appendMessage('error', data);
            currentTaskDiv = null;
        }
    }

    function ensureCurrentTaskDiv() {
        if (!currentTaskDiv) {
            currentTaskDiv = document.createElement('div');
            currentTaskDiv.className = 'message system';
            chatBox.appendChild(currentTaskDiv);
        }
    }

    function appendMessage(role, text) {
        const div = document.createElement('div');
        div.className = `message ${role}`;

        if (role === 'system' && text.includes('Task started')) {
            div.innerHTML = `<strong>${text}</strong>`;
        } else if (role === 'system') {
            div.innerHTML = marked.parse(text);
        } else {
            div.textContent = text;
        }

        chatBox.appendChild(div);
        scrollToBottom();
    }

    function scrollToBottom() {
        chatBox.scrollTop = chatBox.scrollHeight;
    }
});
