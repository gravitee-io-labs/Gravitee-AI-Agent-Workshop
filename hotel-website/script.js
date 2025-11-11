// Configuration
let config = {
    agentCardUrl: window.APP_CONFIG?.agentCardUrl || 'http://localhost:8082/bookings-agent/.well-known/agent-card.json',
    oidcUrl: window.APP_CONFIG?.oidcUrl || 'http://localhost:8092/gravitee/oidc/.well-known/openid-configuration',
    clientId: window.APP_CONFIG?.clientId || 'gravitee-hotels',
    clientSecret: window.APP_CONFIG?.clientSecret || 'gravitee-hotels',
    redirectUri: window.APP_CONFIG?.redirectUri || 'http://localhost:8002/',
    agentUrl: null,
    oidcConfig: null,
    isConnected: false,
    accessToken: null,
    userInfo: null,
    userManager: null
};

// State
let contextId = null;
let isTyping = false;
let isChatMaximized = false;
let requestHistory = [];

// DOM Elements
const elements = {
    // Navigation
    settingsBtn: document.getElementById('settingsBtn'),
    signInBtn: document.getElementById('signInBtn'),
    userMenu: document.getElementById('userMenu'),
    userMenuBtn: document.getElementById('userMenuBtn'),
    userDropdown: document.getElementById('userDropdown'),
    userDisplayName: document.getElementById('userDisplayName'),
    userDropdownEmail: document.getElementById('userDropdownEmail'),
    logoutBtn: document.getElementById('logoutBtn'),
    
    // Hero
    openChatBtn: document.getElementById('openChatBtn'),
    
    // Chat
    chatWidgetBtn: document.getElementById('chatWidgetBtn'),
    chatWindow: document.getElementById('chatWindow'),
    closeChatBtn: document.getElementById('closeChatBtn'),
    toggleChatSizeBtn: document.getElementById('toggleChatSizeBtn'),
    chatMessages: document.getElementById('chatMessages'),
    chatInput: document.getElementById('chatInput'),
    sendBtn: document.getElementById('sendBtn'),
    chatStatus: document.getElementById('chatStatus'),
    
    // Settings Modal
    settingsModal: document.getElementById('settingsModal'),
    settingsOverlay: document.getElementById('settingsOverlay'),
    closeSettingsBtn: document.getElementById('closeSettingsBtn'),
    cancelSettingsBtn: document.getElementById('cancelSettingsBtn'),
    saveSettingsBtn: document.getElementById('saveSettingsBtn'),
    agentCardUrl: document.getElementById('agentCardUrl'),
    oidcUrl: document.getElementById('oidcUrl'),
    clientId: document.getElementById('clientId'),
    clientSecret: document.getElementById('clientSecret'),
    connectAgentBtn: document.getElementById('connectAgentBtn'),
    testOidcBtn: document.getElementById('testOidcBtn'),
    agentConnectionStatus: document.getElementById('agentConnectionStatus'),
    oidcConnectionStatus: document.getElementById('oidcConnectionStatus'),
    agentDebugInfo: document.getElementById('agentDebugInfo'),
    oidcDebugInfo: document.getElementById('oidcDebugInfo'),
    
    // Debug Panel
    debugPanel: document.getElementById('debugPanel'),
    toggleDebugBtn: document.getElementById('toggleDebugBtn'),
    clearDebugBtn: document.getElementById('clearDebugBtn'),
    debugPanelList: document.getElementById('debugPanelList'),
    debugPanelEmpty: document.getElementById('debugPanelEmpty'),
    debugPanelCount: document.getElementById('debugPanelCount'),
    debugPanelResizeHandle: document.getElementById('debugPanelResizeHandle')
};

// Initialize
document.addEventListener('DOMContentLoaded', () => {
    console.log('DOM Content Loaded - Initializing...');
    
    // Verify critical elements exist
    const criticalElements = ['settingsBtn', 'connectAgentBtn', 'testOidcBtn', 'chatWindow'];
    criticalElements.forEach(id => {
        const element = elements[id];
        if (!element) {
            console.error(`Critical element missing: ${id}`);
        } else {
            console.log(`Element found: ${id}`);
        }
    });
    
    initializeEventListeners();
    loadConfigFromStorage();
    
    // Check for OIDC callback
    handleOAuthCallback();
    
    console.log('Initialization complete');
});

function initializeEventListeners() {
    console.log('Setting up event listeners...');
    
    // Settings
    if (elements.settingsBtn) elements.settingsBtn.addEventListener('click', openSettings);
    if (elements.closeSettingsBtn) elements.closeSettingsBtn.addEventListener('click', closeSettings);
    if (elements.cancelSettingsBtn) elements.cancelSettingsBtn.addEventListener('click', closeSettings);
    if (elements.settingsOverlay) elements.settingsOverlay.addEventListener('click', closeSettings);
    if (elements.saveSettingsBtn) elements.saveSettingsBtn.addEventListener('click', saveSettings);
    
    if (elements.connectAgentBtn) {
        elements.connectAgentBtn.addEventListener('click', () => {
            console.log('Connect Agent button clicked');
            testAgentConnection();
        });
        console.log('Agent connection button listener attached');
    } else {
        console.error('connectAgentBtn element not found!');
    }
    
    if (elements.testOidcBtn) {
        elements.testOidcBtn.addEventListener('click', () => {
            console.log('Test OIDC button clicked');
            testOidcConnection();
        });
        console.log('OIDC test button listener attached');
    } else {
        console.error('testOidcBtn element not found!');
    }
    
    // Chat
    if (elements.openChatBtn) elements.openChatBtn.addEventListener('click', openChat);
    if (elements.chatWidgetBtn) elements.chatWidgetBtn.addEventListener('click', openChat);
    if (elements.closeChatBtn) elements.closeChatBtn.addEventListener('click', closeChat);
    if (elements.toggleChatSizeBtn) elements.toggleChatSizeBtn.addEventListener('click', toggleChatSize);
    if (elements.sendBtn) elements.sendBtn.addEventListener('click', sendMessage);
    if (elements.chatInput) {
        elements.chatInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') sendMessage();
        });
    }
    
    // Auth
    if (elements.signInBtn) elements.signInBtn.addEventListener('click', login);
    if (elements.userMenuBtn) elements.userMenuBtn.addEventListener('click', toggleUserDropdown);
    if (elements.logoutBtn) elements.logoutBtn.addEventListener('click', logout);
    
    // Close dropdown when clicking outside
    document.addEventListener('click', (e) => {
        if (!elements.userMenu?.contains(e.target)) {
            elements.userDropdown?.classList.add('hidden');
        }
    });
    
    // Debug Panel
    if (elements.toggleDebugBtn) {
        elements.toggleDebugBtn.addEventListener('click', (e) => {
            e.stopPropagation(); // Prevent header click
            toggleDebugPanel();
        });
    }
    if (elements.clearDebugBtn) {
        elements.clearDebugBtn.addEventListener('click', (e) => {
            e.stopPropagation(); // Prevent header click
            clearDebugHistory();
        });
    }
    if (elements.debugPanel) {
        elements.debugPanel.querySelector('.debug-panel-header').addEventListener('click', toggleDebugPanel);
    }
    if (elements.debugPanelResizeHandle) {
        initializeDebugPanelResize();
    }
    
    console.log('Event listeners setup complete');
}

// Settings Functions
function openSettings() {
    elements.settingsModal.classList.remove('hidden');
    
    // Set placeholders from config
    elements.agentCardUrl.placeholder = config.agentCardUrl;
    elements.oidcUrl.placeholder = config.oidcUrl;
    elements.clientId.placeholder = config.clientId;
    elements.clientSecret.placeholder = config.clientSecret;
    
    // Set values
    elements.agentCardUrl.value = config.agentCardUrl;
    elements.oidcUrl.value = config.oidcUrl;
    elements.clientId.value = config.clientId;
    elements.clientSecret.value = config.clientSecret;
    
    updateAgentConnectionStatus();
    updateOidcConnectionStatus();
}

function closeSettings() {
    elements.settingsModal.classList.add('hidden');
}

async function saveSettings() {
    config.agentCardUrl = elements.agentCardUrl.value.trim();
    config.oidcUrl = elements.oidcUrl.value.trim();
    config.clientId = elements.clientId.value.trim();
    config.clientSecret = elements.clientSecret.value.trim();
    saveConfigToStorage();
    showDebugInfo(elements.agentDebugInfo, 'Settings saved successfully', 'success');
}

async function testAgentConnection() {
    console.log('testAgentConnection called');
    const url = elements.agentCardUrl.value.trim();
    console.log('Agent URL:', url);
    
    if (!url) {
        showDebugInfo(elements.agentDebugInfo, 'Please enter an Agent Card URL', 'error');
        return;
    }
    
    updateAgentConnectionStatus('connecting');
    elements.connectAgentBtn.disabled = true;
    
    try {
        console.log('Fetching agent card from:', url);
        const response = await fetch(url);
        console.log('Response status:', response.status);
        
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }
        
        const agentCard = await response.json();
        console.log('Agent card received:', agentCard);
        
        config.agentUrl = agentCard.url;
        config.isConnected = true;
        
        updateAgentConnectionStatus('connected');
        enableChat();
        
        const debugMsg = `✅ Successfully connected to agent!\n\nAgent Name: ${agentCard.name}\nAgent URL: ${agentCard.url}\nVersion: ${agentCard.version}`;
        showDebugInfo(elements.agentDebugInfo, debugMsg, 'success');
        
        console.log('Connected to agent:', agentCard);
    } catch (error) {
        console.error('Agent connection error:', error);
        config.isConnected = false;
        updateAgentConnectionStatus('failed');
        disableChat();
        
        const debugMsg = `❌ Connection failed:\n\n${error.message}\n\nURL: ${url}\n\nPlease verify:\n- The URL is correct\n- The agent is running\n- CORS is properly configured`;
        showDebugInfo(elements.agentDebugInfo, debugMsg, 'error');
    } finally {
        elements.connectAgentBtn.disabled = false;
    }
}

async function testOidcConnection() {
    console.log('testOidcConnection called');
    const url = elements.oidcUrl.value.trim();
    console.log('OIDC URL:', url);
    
    if (!url) {
        showDebugInfo(elements.oidcDebugInfo, 'Please enter an OIDC Discovery URL', 'error');
        return;
    }
    
    updateOidcConnectionStatus('connecting');
    elements.testOidcBtn.disabled = true;
    
    try {
        console.log('Fetching OIDC config from:', url);
        const response = await fetch(url);
        console.log('Response status:', response.status);
        
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }
        
        const oidcConfig = await response.json();
        console.log('OIDC config received:', oidcConfig);
        
        config.oidcConfig = oidcConfig;
        
        updateOidcConnectionStatus('connected');
        
        const debugMsg = `✅ Successfully connected to OIDC provider!\n\nIssuer: ${oidcConfig.issuer}\nAuthorization Endpoint: ${oidcConfig.authorization_endpoint}\nToken Endpoint: ${oidcConfig.token_endpoint}`;
        showDebugInfo(elements.oidcDebugInfo, debugMsg, 'success');
        
        console.log('Connected to OIDC:', oidcConfig);
    } catch (error) {
        console.error('OIDC connection error:', error);
        updateOidcConnectionStatus('failed');
        
        const debugMsg = `❌ Connection failed:\n\n${error.message}\n\nURL: ${url}\n\nPlease verify:\n- The URL is correct\n- The OIDC provider is running\n- CORS is properly configured`;
        showDebugInfo(elements.oidcDebugInfo, debugMsg, 'error');
    } finally {
        elements.testOidcBtn.disabled = false;
    }
}

function updateAgentConnectionStatus(status = null) {
    if (status === null) {
        status = config.isConnected ? 'connected' : 'disconnected';
    }
    
    const indicator = elements.agentConnectionStatus.querySelector('.status-indicator');
    const text = elements.agentConnectionStatus.querySelector('span');
    
    indicator.className = 'status-indicator';
    
    switch(status) {
        case 'connected':
            indicator.classList.add('status-connected');
            text.textContent = 'Test successful';
            elements.chatStatus.textContent = 'Online';
            break;
        case 'connecting':
            indicator.classList.add('status-connecting');
            text.textContent = 'Testing...';
            elements.chatStatus.textContent = 'Connecting...';
            break;
        case 'disconnected':
            indicator.classList.add('status-disconnected');
            text.textContent = 'Not tested';
            elements.chatStatus.textContent = 'Offline';
            break;
        case 'failed':
            indicator.classList.add('status-disconnected');
            text.textContent = 'Test failed';
            elements.chatStatus.textContent = 'Offline';
            break;
    }
}

function updateOidcConnectionStatus(status = 'disconnected') {
    const indicator = elements.oidcConnectionStatus.querySelector('.status-indicator');
    const text = elements.oidcConnectionStatus.querySelector('span');
    
    indicator.className = 'status-indicator';
    
    switch(status) {
        case 'connected':
            indicator.classList.add('status-connected');
            text.textContent = 'Test successful';
            break;
        case 'connecting':
            indicator.classList.add('status-connecting');
            text.textContent = 'Testing...';
            break;
        case 'disconnected':
            indicator.classList.add('status-disconnected');
            text.textContent = 'Not tested';
            break;
        case 'failed':
            indicator.classList.add('status-disconnected');
            text.textContent = 'Test failed';
            break;
    }
}

function showDebugInfo(element, message, type = 'error') {
    const pre = element.querySelector('pre');
    pre.textContent = message;
    element.className = 'debug-info';
    if (type === 'success') {
        element.classList.add('success');
    }
    element.classList.remove('hidden');
}

// Agent Connection
async function connectToAgent() {
    const startTime = Date.now();
    let requestId = null;
    
    try {
        // Add to debug panel
        requestId = addDebugRequest({
            method: 'GET',
            url: config.agentCardUrl,
            headers: { 'Accept': 'application/json' },
            requestBody: null
        });
        
        // Fetch agent card
        const response = await fetch(config.agentCardUrl);
        const duration = Date.now() - startTime;
        
        // Extract response headers
        const responseHeaders = {};
        response.headers.forEach((value, key) => {
            responseHeaders[key] = value;
        });
        
        if (!response.ok) {
            updateDebugRequest(requestId, {
                status: response.status,
                duration: duration,
                responseHeaders: responseHeaders,
                responseBody: { error: response.statusText }
            });
            throw new Error(`Failed to fetch agent card: ${response.statusText}`);
        }
        
        const agentCard = await response.json();
        
        // Update debug panel with success
        updateDebugRequest(requestId, {
            status: response.status,
            duration: duration,
            responseHeaders: responseHeaders,
            responseBody: agentCard
        });
        
        config.agentUrl = agentCard.url;
        config.isConnected = true;
        
        updateAgentConnectionStatus('connected');
        enableChat();
        
        console.log('Connected to agent:', agentCard.name);
        return agentCard;
    } catch (error) {
        const duration = Date.now() - startTime;
        
        if (requestId) {
            updateDebugRequest(requestId, {
                status: 0,
                duration: duration,
                responseBody: { error: error.message }
            });
        }
        
        config.isConnected = false;
        updateAgentConnectionStatus('disconnected');
        disableChat();
        throw error;
    }
}

// Chat Functions
function openChat() {
    elements.chatWindow.classList.remove('hidden');
    elements.chatWidgetBtn.style.display = 'none';
    
    // Connect to agent if not connected
    if (!config.isConnected) {
        connectToAgent().catch(error => {
            console.error('Auto-connect failed:', error);
            addMessage('agent', 'Failed to connect to AI Agent. Please check your settings.');
        });
    }
}

function closeChat() {
    elements.chatWindow.classList.add('hidden');
    elements.chatWidgetBtn.style.display = 'flex';
}

function toggleChatSize() {
    isChatMaximized = !isChatMaximized;
    if (isChatMaximized) {
        elements.chatWindow.classList.add('maximized');
        elements.toggleChatSizeBtn.innerHTML = `
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M8 3v3a2 2 0 0 1-2 2H3m18 0h-3a2 2 0 0 1-2-2V3m0 18v-3a2 2 0 0 1 2-2h3M3 16h3a2 2 0 0 1 2 2v3"></path>
            </svg>
        `;
        elements.toggleChatSizeBtn.title = 'Minimize';
    } else {
        elements.chatWindow.classList.remove('maximized');
        elements.toggleChatSizeBtn.innerHTML = `
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M8 3H5a2 2 0 0 0-2 2v3m18 0V5a2 2 0 0 0-2-2h-3m0 18h3a2 2 0 0 0 2-2v-3M3 16v3a2 2 0 0 0 2 2h3"></path>
            </svg>
        `;
        elements.toggleChatSizeBtn.title = 'Maximize';
    }
}

function enableChat() {
    elements.chatInput.disabled = false;
    elements.sendBtn.disabled = false;
}

function disableChat() {
    elements.chatInput.disabled = true;
    elements.sendBtn.disabled = true;
}

async function sendMessage() {
    const message = elements.chatInput.value.trim();
    if (!message || !config.isConnected) return;
    
    // Add user message to chat
    addMessage('user', message);
    elements.chatInput.value = '';
    
    // Show typing indicator
    showTypingIndicator();
    
    try {
        // Send message to agent
        const response = await sendToAgent(message);
        
        // Remove typing indicator
        hideTypingIndicator();
        
        // Add agent response
        if (response && response.parts) {
            const textPart = response.parts.find(p => p.text);
            if (textPart) {
                addMessage('agent', textPart.text);
            }
        }
    } catch (error) {
        hideTypingIndicator();
        console.error('Error sending message:', error);
        addMessage('agent', 'Sorry, I encountered an error. Please try again.');
    }
}

async function sendToAgent(message) {
    const startTime = Date.now();
    let requestId = null;
    
    try {
        const headers = {
            'Content-Type': 'application/json'
        };
        
        // Add auth token if available
        if (config.accessToken) {
            headers['Authorization'] = `Bearer ${config.accessToken}`;
        }
        
        const messageObj = {
            role: 'user',
            parts: [{ text: message }],
            messageId: generateId()
        };
        
        // Add context ID if available
        if (contextId) {
            messageObj.contextId = contextId;
        }
        
        const payload = {
            method: 'message/send',
            jsonrpc: '2.0',
            id: generateId(),
            params: {
                message: messageObj
            }
        };
        
        console.log('Sending to agent:', JSON.stringify(payload, null, 2));
        
        // Add to debug panel
        requestId = addDebugRequest({
            method: 'POST',
            url: config.agentUrl,
            headers: headers,
            requestBody: payload,
            contextId: contextId
        });
        
        const response = await fetch(config.agentUrl, {
            method: 'POST',
            headers: headers,
            body: JSON.stringify(payload)
        });
        
        const duration = Date.now() - startTime;
        
        // Extract response headers
        const responseHeaders = {};
        response.headers.forEach((value, key) => {
            responseHeaders[key] = value;
        });
        
        if (!response.ok) {
            // Update debug panel with error
            updateDebugRequest(requestId, {
                status: response.status,
                duration: duration,
                responseHeaders: responseHeaders,
                responseBody: { error: response.statusText }
            });
            throw new Error(`Agent request failed: ${response.statusText}`);
        }
        
        const data = await response.json();
        console.log('Agent response:', data);
        
        // Update debug panel with success
        updateDebugRequest(requestId, {
            status: response.status,
            duration: duration,
            responseHeaders: responseHeaders,
            responseBody: data
        });
        
        // Extract context ID from the response
        if (data.result && data.result.contextId) {
            contextId = data.result.contextId;
            console.log('Context ID updated:', contextId);
        }
        
        return data.result;
    } catch (error) {
        const duration = Date.now() - startTime;
        
        // Update debug panel with error if we have a requestId
        if (requestId) {
            updateDebugRequest(requestId, {
                status: 0,
                duration: duration,
                responseBody: { error: error.message }
            });
        }
        
        console.error('Error communicating with agent:', error);
        throw error;
    }
}

function addMessage(sender, content) {
    const messageDiv = document.createElement('div');
    messageDiv.className = `message ${sender}-message`;
    
    const avatarDiv = document.createElement('div');
    avatarDiv.className = 'message-avatar';
    
    if (sender === 'agent') {
        const img = document.createElement('img');
        img.src = 'assets/gravitee-logo/Mark.svg';
        img.alt = 'Bot';
        avatarDiv.appendChild(img);
    } else {
        // User icon SVG
        avatarDiv.innerHTML = `
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"></path>
                <circle cx="12" cy="7" r="4"></circle>
            </svg>
        `;
    }
    
    const contentDiv = document.createElement('div');
    contentDiv.className = 'message-content';
    
    // Simple markdown-like parsing
    let formattedContent = content;
    formattedContent = formattedContent.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
    formattedContent = formattedContent.replace(/\*(.*?)\*/g, '<em>$1</em>');
    formattedContent = formattedContent.replace(/\n/g, '<br>');
    
    contentDiv.innerHTML = `<p>${formattedContent}</p>`;
    
    messageDiv.appendChild(avatarDiv);
    messageDiv.appendChild(contentDiv);
    
    elements.chatMessages.appendChild(messageDiv);
    elements.chatMessages.scrollTop = elements.chatMessages.scrollHeight;
}

function showTypingIndicator() {
    if (isTyping) return;
    isTyping = true;
    
    const typingDiv = document.createElement('div');
    typingDiv.className = 'message agent-message typing-message';
    typingDiv.id = 'typingIndicator';
    
    const avatarDiv = document.createElement('div');
    avatarDiv.className = 'message-avatar';
    const img = document.createElement('img');
    img.src = 'assets/gravitee-logo/Mark.svg';
    img.alt = 'Bot';
    avatarDiv.appendChild(img);
    
    const contentDiv = document.createElement('div');
    contentDiv.className = 'message-content';
    
    const typingIndicator = document.createElement('div');
    typingIndicator.className = 'typing-indicator';
    typingIndicator.innerHTML = '<div class="typing-dot"></div><div class="typing-dot"></div><div class="typing-dot"></div>';
    
    contentDiv.appendChild(typingIndicator);
    typingDiv.appendChild(avatarDiv);
    typingDiv.appendChild(contentDiv);
    
    elements.chatMessages.appendChild(typingDiv);
    elements.chatMessages.scrollTop = elements.chatMessages.scrollHeight;
}

function hideTypingIndicator() {
    isTyping = false;
    const typingIndicator = document.getElementById('typingIndicator');
    if (typingIndicator) {
        typingIndicator.remove();
    }
}

// Authentication Functions
function toggleUserDropdown() {
    elements.userDropdown.classList.toggle('hidden');
}

async function login() {
    try {
        // Load OIDC configuration if not already loaded
        if (!config.oidcConfig) {
            const response = await fetch(config.oidcUrl);
            if (!response.ok) {
                throw new Error('Failed to fetch OIDC configuration');
            }
            config.oidcConfig = await response.json();
        }

        // Generate PKCE code verifier and challenge
        const codeVerifier = generateCodeVerifier();
        const codeChallenge = await generateCodeChallenge(codeVerifier);
        
        // Store code verifier for later use
        sessionStorage.setItem('code_verifier', codeVerifier);
        sessionStorage.setItem('auth_state', generateRandomString(16));
        
        // Build authorization URL
        const authParams = new URLSearchParams({
            client_id: config.clientId,
            redirect_uri: config.redirectUri,
            response_type: 'code',
            scope: 'openid profile email',
            code_challenge: codeChallenge,
            code_challenge_method: 'S256',
            state: sessionStorage.getItem('auth_state')
        });
        
        const authUrl = `${config.oidcConfig.authorization_endpoint}?${authParams.toString()}`;
        console.log('Redirecting to:', authUrl);
        
        // Redirect to authorization endpoint
        window.location.href = authUrl;
    } catch (error) {
        console.error('Login error:', error);
        alert('Failed to initiate login. Please check your OIDC configuration in settings.');
    }
}

async function handleOAuthCallback() {
    const params = new URLSearchParams(window.location.search);
    const code = params.get('code');
    const state = params.get('state');
    
    if (!code) {
        // Not a callback, check if we have stored auth
        checkStoredAuth();
        return;
    }
    
    // Verify state
    const storedState = sessionStorage.getItem('auth_state');
    if (state !== storedState) {
        console.error('State mismatch - possible CSRF attack');
        window.history.replaceState({}, document.title, window.location.pathname);
        return;
    }
    
    try {
        // Load OIDC configuration
        const oidcResponse = await fetch(config.oidcUrl);
        if (!oidcResponse.ok) {
            throw new Error('Failed to fetch OIDC configuration');
        }
        config.oidcConfig = await oidcResponse.json();
        
        // Get code verifier
        const codeVerifier = sessionStorage.getItem('code_verifier');
        if (!codeVerifier) {
            throw new Error('Code verifier not found');
        }
        
        // Exchange code for token
        const tokenParams = new URLSearchParams({
            grant_type: 'authorization_code',
            code: code,
            redirect_uri: config.redirectUri,
            client_id: config.clientId,
            code_verifier: codeVerifier
        });
        
        const tokenResponse = await fetch(config.oidcConfig.token_endpoint, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/x-www-form-urlencoded'
            },
            body: tokenParams.toString()
        });
        
        if (!tokenResponse.ok) {
            const errorText = await tokenResponse.text();
            throw new Error(`Token exchange failed: ${errorText}`);
        }
        
        const tokenData = await tokenResponse.json();
        config.accessToken = tokenData.access_token;
        const idToken = tokenData.id_token;
        
        // Fetch user info
        const userInfoResponse = await fetch(config.oidcConfig.userinfo_endpoint, {
            headers: {
                'Authorization': `Bearer ${config.accessToken}`
            }
        });
        
        if (userInfoResponse.ok) {
            const userInfo = await userInfoResponse.json();
            config.userInfo = {
                email: userInfo.preferred_username,
                given_name: userInfo.given_name,
                family_name: userInfo.family_name
            };
            
            // Store in localStorage
            localStorage.setItem('access_token', config.accessToken);
            if (idToken) {
                localStorage.setItem('id_token', idToken);
            }
            localStorage.setItem('user_info', JSON.stringify(config.userInfo));
            
            updateUserDisplay();
            console.log('Successfully logged in:', config.userInfo);
        }
        
        // Clean up
        sessionStorage.removeItem('code_verifier');
        sessionStorage.removeItem('auth_state');
        window.history.replaceState({}, document.title, window.location.pathname);
        
    } catch (error) {
        console.error('OAuth callback error:', error);
        alert('Authentication failed: ' + error.message);
        sessionStorage.removeItem('code_verifier');
        sessionStorage.removeItem('auth_state');
        window.history.replaceState({}, document.title, window.location.pathname);
    }
}

async function checkStoredAuth() {
    const token = localStorage.getItem('access_token');
    const userInfo = localStorage.getItem('user_info');
    
    if (token && userInfo) {
        config.accessToken = token;
        config.userInfo = JSON.parse(userInfo);
        updateUserDisplay();
        console.log('Restored auth from storage');
        
        // Load OIDC config in background for logout functionality
        if (!config.oidcConfig) {
            try {
                const response = await fetch(config.oidcUrl);
                if (response.ok) {
                    config.oidcConfig = await response.json();
                    console.log('OIDC config loaded for logout');
                }
            } catch (error) {
                console.error('Failed to load OIDC config:', error);
            }
        }
    } else {
        updateUserDisplay();
    }
}

function logout() {
    // Get the end_session_endpoint from OIDC config
    const endSessionEndpoint = config.oidcConfig?.end_session_endpoint;
    const idToken = localStorage.getItem('id_token');
    
    // Clear local state first
    config.accessToken = null;
    config.userInfo = null;
    localStorage.removeItem('access_token');
    localStorage.removeItem('id_token');
    localStorage.removeItem('user_info');
    updateUserDisplay();
    elements.userDropdown.classList.add('hidden');
    
    console.log('Logged out locally');
    
    // If we have an end_session_endpoint, redirect to it for proper logout
    if (endSessionEndpoint) {
        const logoutParams = new URLSearchParams({
            post_logout_redirect_uri: config.redirectUri,
            client_id: config.clientId
        });
        
        // Add id_token_hint if available (recommended by OIDC spec)
        if (idToken) {
            logoutParams.append('id_token_hint', idToken);
        }
        
        const logoutUrl = `${endSessionEndpoint}?${logoutParams.toString()}`;
        console.log('Redirecting to logout endpoint:', logoutUrl);
        
        // Redirect to IAM logout endpoint
        window.location.href = logoutUrl;
    } else {
        console.log('No end_session_endpoint available, local logout only');
    }
}

function updateUserDisplay() {
    if (config.userInfo && config.accessToken) {
        // User is logged in
        elements.signInBtn?.classList.add('hidden');
        elements.userMenu?.classList.remove('hidden');
        
        const firstName = config.userInfo.given_name || 'User';
        const lastName = config.userInfo.family_name || '';
        const lastInitial = lastName ? lastName.charAt(0).toUpperCase() : '';
        const displayName = `${firstName} ${lastInitial}${lastInitial ? '.' : ''}`;
        
        elements.userDisplayName.textContent = displayName;
        elements.userDropdownEmail.textContent = config.userInfo.email || '';
    } else {
        // User is not logged in
        elements.signInBtn?.classList.remove('hidden');
        elements.userMenu?.classList.add('hidden');
        elements.userDropdown?.classList.add('hidden');
    }
}

// PKCE Helper Functions
function generateCodeVerifier() {
    const array = new Uint8Array(32);
    crypto.getRandomValues(array);
    return base64URLEncode(array);
}

async function generateCodeChallenge(verifier) {
    const encoder = new TextEncoder();
    const data = encoder.encode(verifier);
    const hash = await crypto.subtle.digest('SHA-256', data);
    return base64URLEncode(new Uint8Array(hash));
}

function base64URLEncode(buffer) {
    const base64 = btoa(String.fromCharCode(...buffer));
    return base64
        .replace(/\+/g, '-')
        .replace(/\//g, '_')
        .replace(/=/g, '');
}

function generateRandomString(length) {
    const array = new Uint8Array(length);
    crypto.getRandomValues(array);
    return Array.from(array, byte => byte.toString(16).padStart(2, '0')).join('');
}

// Storage Functions
function saveConfigToStorage() {
    localStorage.setItem('agent_card_url', config.agentCardUrl);
    localStorage.setItem('oidc_url', config.oidcUrl);
    localStorage.setItem('client_id', config.clientId);
    localStorage.setItem('client_secret', config.clientSecret);
}

function loadConfigFromStorage() {
    const savedAgentCardUrl = localStorage.getItem('agent_card_url');
    const savedOidcUrl = localStorage.getItem('oidc_url');
    const savedClientId = localStorage.getItem('client_id');
    const savedClientSecret = localStorage.getItem('client_secret');
    
    if (savedAgentCardUrl) config.agentCardUrl = savedAgentCardUrl;
    if (savedOidcUrl) config.oidcUrl = savedOidcUrl;
    if (savedClientId) config.clientId = savedClientId;
    if (savedClientSecret) config.clientSecret = savedClientSecret;
}

// Utility Functions
function generateId() {
    return `${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
}

// Debug Panel Functions
function toggleDebugPanel() {
    elements.debugPanel.classList.toggle('collapsed');
}

function clearDebugHistory() {
    requestHistory = [];
    updateDebugPanel();
}

function initializeDebugPanelResize() {
    let isResizing = false;
    let startY = 0;
    let startHeight = 0;
    const minHeight = 100;
    
    const getMaxHeight = () => window.innerHeight - 100;
    
    elements.debugPanelResizeHandle.addEventListener('mousedown', (e) => {
        // Don't allow resizing when panel is collapsed
        if (elements.debugPanel.classList.contains('collapsed')) {
            return;
        }
        
        isResizing = true;
        startY = e.clientY;
        startHeight = elements.debugPanel.offsetHeight;
        
        elements.debugPanel.classList.add('resizing');
        document.body.style.cursor = 'ns-resize';
        document.body.style.userSelect = 'none';
        
        e.preventDefault();
        e.stopPropagation(); // Prevent header click
    });
    
    document.addEventListener('mousemove', (e) => {
        if (!isResizing) return;
        
        const deltaY = startY - e.clientY;
        let newHeight = startHeight + deltaY;
        
        // Constrain height dynamically
        const maxHeight = getMaxHeight();
        newHeight = Math.max(minHeight, Math.min(newHeight, maxHeight));
        
        elements.debugPanel.style.height = `${newHeight}px`;
        
        e.preventDefault();
    });
    
    document.addEventListener('mouseup', () => {
        if (isResizing) {
            isResizing = false;
            elements.debugPanel.classList.remove('resizing');
            document.body.style.cursor = '';
            document.body.style.userSelect = '';
        }
    });
    
    // Handle window resize
    window.addEventListener('resize', () => {
        const currentHeight = parseInt(elements.debugPanel.style.height) || elements.debugPanel.offsetHeight;
        const maxHeight = getMaxHeight();
        
        if (currentHeight > maxHeight) {
            elements.debugPanel.style.height = `${maxHeight}px`;
        }
    });
}

function addDebugRequest(requestData) {
    const request = {
        id: generateId(),
        timestamp: new Date().toISOString(),
        ...requestData
    };
    
    requestHistory.unshift(request);
    
    // Keep only last 50 requests
    if (requestHistory.length > 50) {
        requestHistory = requestHistory.slice(0, 50);
    }
    
    updateDebugPanel();
    
    return request.id;
}

function updateDebugRequest(id, updates) {
    const request = requestHistory.find(r => r.id === id);
    if (request) {
        Object.assign(request, updates);
        updateDebugPanel();
    }
}

function updateDebugPanel() {
    if (!elements.debugPanelList || !elements.debugPanelEmpty || !elements.debugPanelCount) return;
    
    // Update count
    elements.debugPanelCount.textContent = `${requestHistory.length} request${requestHistory.length !== 1 ? 's' : ''}`;
    
    // Show/hide empty state
    if (requestHistory.length === 0) {
        elements.debugPanelEmpty.classList.remove('hidden');
        elements.debugPanelList.innerHTML = '';
        return;
    }
    
    elements.debugPanelEmpty.classList.add('hidden');
    
    // Render request list
    elements.debugPanelList.innerHTML = requestHistory.map(request => {
        const duration = request.duration ? `${request.duration}ms` : 'Pending...';
        const statusClass = request.status ? (request.status < 400 ? 'success' : 'error') : 'pending';
        const statusText = request.status || 'Pending';
        
        const time = new Date(request.timestamp);
        const timeStr = time.toLocaleTimeString();
        
        return `
            <div class="debug-request" data-request-id="${request.id}">
                <div class="debug-request-header" onclick="toggleDebugRequest('${request.id}')">
                    <div class="debug-request-info">
                        <span class="debug-request-method">${request.method}</span>
                        <span class="debug-request-url">${request.url}</span>
                        <span class="debug-request-time">${timeStr}</span>
                    </div>
                    <div class="debug-request-status">
                        <span class="debug-request-status-badge ${statusClass}">${statusText}</span>
                        <span class="debug-request-duration">${duration}</span>
                        <svg class="debug-request-toggle" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <polyline points="6 9 12 15 18 9"></polyline>
                        </svg>
                    </div>
                </div>
                <div class="debug-request-body">
                    <div class="debug-request-tabs">
                        <div class="debug-request-tab active" onclick="switchDebugTab('${request.id}', 'metadata')">Metadata</div>
                        <div class="debug-request-tab" onclick="switchDebugTab('${request.id}', 'request')">Request</div>
                        <div class="debug-request-tab" onclick="switchDebugTab('${request.id}', 'response')">Response</div>
                    </div>
                    <div class="debug-request-tab-content active" data-tab="metadata">
                        ${formatMetadata(request)}
                    </div>
                    <div class="debug-request-tab-content" data-tab="request">
                        ${formatRequestColumns(request)}
                    </div>
                    <div class="debug-request-tab-content" data-tab="response">
                        ${formatResponseColumns(request)}
                    </div>
                </div>
            </div>
        `;
    }).join('');
}

function toggleDebugRequest(id) {
    const element = document.querySelector(`[data-request-id="${id}"]`);
    if (element) {
        element.classList.toggle('expanded');
    }
}

function switchDebugTab(requestId, tabName) {
    const element = document.querySelector(`[data-request-id="${requestId}"]`);
    if (!element) return;
    
    // Update tabs
    element.querySelectorAll('.debug-request-tab').forEach(tab => {
        tab.classList.remove('active');
    });
    element.querySelector(`[onclick*="${tabName}"]`).classList.add('active');
    
    // Update content
    element.querySelectorAll('.debug-request-tab-content').forEach(content => {
        content.classList.remove('active');
    });
    element.querySelector(`[data-tab="${tabName}"]`).classList.add('active');
}

function formatHeaders(headers) {
    if (!headers || Object.keys(headers).length === 0) {
        return '<div class="debug-code-block"><pre>No headers</pre></div>';
    }
    
    const formatted = Object.entries(headers)
        .map(([key, value]) => `${key}: ${value}`)
        .join('\n');
    
    return `<div class="debug-code-block"><pre>${escapeHtml(formatted)}</pre></div>`;
}

function formatJson(data) {
    if (!data) {
        return '<div class="debug-code-block"><pre>No data</pre></div>';
    }
    
    try {
        const formatted = typeof data === 'string' ? data : JSON.stringify(data, null, 2);
        return `<div class="debug-code-block"><pre>${escapeHtml(formatted)}</pre></div>`;
    } catch (e) {
        return `<div class="debug-code-block"><pre>Error formatting data: ${escapeHtml(String(data))}</pre></div>`;
    }
}

function formatRequestColumns(request) {
    return `
        <div class="debug-two-columns">
            <div class="debug-column">
                <h4 class="debug-column-header">Headers</h4>
                ${formatHeaders(request.headers)}
            </div>
            <div class="debug-column">
                <h4 class="debug-column-header">Payload</h4>
                ${formatJson(request.requestBody)}
            </div>
        </div>
    `;
}

function formatResponseColumns(request) {
    return `
        <div class="debug-two-columns">
            <div class="debug-column">
                <h4 class="debug-column-header">Headers</h4>
                ${formatHeaders(request.responseHeaders)}
            </div>
            <div class="debug-column">
                <h4 class="debug-column-header">Payload</h4>
                ${formatJson(request.responseBody)}
            </div>
        </div>
    `;
}

function formatMetadata(request) {
    const metadata = [
        ['Request ID', request.id],
        ['Timestamp', new Date(request.timestamp).toLocaleString()],
        ['Method', request.method],
        ['URL', request.url],
        ['Status', request.status || 'Pending'],
        ['Duration', request.duration ? `${request.duration}ms` : 'Pending'],
        ['Context ID', request.contextId || 'N/A']
    ];
    
    return `
        <div class="debug-metadata">
            ${metadata.map(([label, value]) => `
                <div class="debug-metadata-label">${label}:</div>
                <div class="debug-metadata-value">${escapeHtml(String(value))}</div>
            `).join('')}
        </div>
    `;
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// Make functions globally accessible for onclick handlers
window.toggleDebugRequest = toggleDebugRequest;
window.switchDebugTab = switchDebugTab;
