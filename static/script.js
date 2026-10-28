
/*
=========================================================
TripMate AI — Complete Frontend JavaScript
=========================================================

Original features preserved:
1. Travel request submission
2. Quick prompts
3. Loading indicator
4. Error handling
5. Markdown travel plan
6. Copy result
7. Download PDF
8. Ctrl + Enter shortcut

New features:
9. New Chat
10. Recent Chat History
11. Open Previous Chat
12. Follow-up Questions
13. Restore Chat After Refresh
14. Mobile Sidebar

Expected FastAPI endpoints:
POST /api/travel
POST /api/new-chat
GET  /api/chats
GET  /api/chats/{thread_id}
=========================================================
*/


// =====================================================
// 1. GLOBAL VARIABLES
// =====================================================

let currentThreadId = null;
let latestAnswerMarkdown = "";
let isSending = false;
let navigationVersion = 0;


// Helper: Get HTML element by ID

const $ = (id) => document.getElementById(id);


// =====================================================
// 2. QUICK PROMPT
// =====================================================

function setPrompt(text) {
    $("userInput").value = text;
    $("userInput").focus();
}


// =====================================================
// 3. LOADING INDICATOR
// =====================================================

function setLoading(loading) {

    isSending = loading;

    $("sendBtn").disabled = loading;
    $("newChatBtn").disabled = loading;

    $("btnText").classList.toggle(
        "hidden",
        loading
    );

    $("btnLoader").classList.toggle(
        "hidden",
        !loading
    );
}


// =====================================================
// 4. ERROR HANDLING
// =====================================================

function showError(message) {

    const errorBox = $("errorBox");

    errorBox.textContent = message;

    errorBox.classList.remove("hidden");
}


function hideError() {

    const errorBox = $("errorBox");

    errorBox.textContent = "";

    errorBox.classList.add("hidden");
}


// =====================================================
// 5. API REQUEST HELPER
// =====================================================

async function apiRequest(url, options = {}) {

    let response;

    try {

        response = await fetch(url, {
            credentials: "same-origin",
            ...options
        });

    } catch (_) {

        throw new Error(
            "Cannot reach the server. Check that FastAPI is running."
        );
    }

    const data = await response.json().catch(
        () => ({})
    );

    if (!response.ok || data.success === false) {

        const detail =
            data.detail ||
            data.error ||
            `Request failed (${response.status}).`;

        throw new Error(
            typeof detail === "string"
                ? detail
                : "Request failed."
        );
    }

    return data;
}


// =====================================================
// 6. SAFE MARKDOWN RENDERING
// =====================================================

function renderMarkdown(container, markdown) {

    container.replaceChildren();

    if (typeof marked === "undefined") {

        container.textContent = markdown;
        container.style.whiteSpace = "pre-wrap";

        return;
    }

    // Escape raw HTML before converting Markdown.

    const escaped = markdown
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;");

    const parsed = new DOMParser().parseFromString(
        marked.parse(escaped),
        "text/html"
    );

    const allowed = new Set([
        "P",
        "H1",
        "H2",
        "H3",
        "H4",
        "H5",
        "H6",
        "UL",
        "OL",
        "LI",
        "B",
        "STRONG",
        "I",
        "EM",
        "BLOCKQUOTE",
        "PRE",
        "CODE",
        "BR",
        "HR",
        "TABLE",
        "THEAD",
        "TBODY",
        "TR",
        "TH",
        "TD",
        "A",
        "DEL"
    ]);

    function safeCopy(node) {

        if (node.nodeType === Node.TEXT_NODE) {

            return document.createTextNode(
                node.textContent
            );
        }

        if (node.nodeType !== Node.ELEMENT_NODE) {

            return document.createDocumentFragment();
        }

        if (!allowed.has(node.tagName)) {

            return document.createTextNode(
                node.textContent || ""
            );
        }

        const element = document.createElement(
            node.tagName.toLowerCase()
        );

        if (node.tagName === "A") {

            const raw = node.getAttribute("href") || "";

            try {

                const url = new URL(
                    raw,
                    window.location.href
                );

                if (
                    ["https:", "http:", "mailto:"].includes(
                        url.protocol
                    )
                ) {

                    element.href = url.href;
                    element.target = "_blank";
                    element.rel = "noopener noreferrer";
                }

            } catch (_) {

                // Invalid link: display text only.
            }
        }

        for (const child of node.childNodes) {

            element.appendChild(
                safeCopy(child)
            );
        }

        return element;
    }

    for (const child of parsed.body.childNodes) {

        container.appendChild(
            safeCopy(child)
        );
    }
}


// =====================================================
// 7. SHOW TRAVEL PLAN RESULT
// =====================================================

function showResult(answer, threadId, scroll = false) {

    latestAnswerMarkdown = answer || "";

    if (!latestAnswerMarkdown) {

        $("resultSection").classList.add("hidden");

        $("resultBox").replaceChildren();

        return;
    }

    renderMarkdown(
        $("resultBox"),
        latestAnswerMarkdown
    );

    $("threadInfo").textContent =
        `Thread ID: ${threadId || "-"}`;

    $("resultSection").classList.remove("hidden");

    if (scroll) {

        $("resultSection").scrollIntoView({
            behavior: "smooth",
            block: "start"
        });
    }
}


// =====================================================
// 8. CLEAR CONVERSATION VIEW
// =====================================================

function clearConversationView() {

    $("conversationMessages").replaceChildren();

    $("conversationSection").classList.add("hidden");

    $("resultSection").classList.add("hidden");

    $("resultBox").replaceChildren();

    $("threadInfo").textContent = "Thread ID: -";

    $("userInput").value = "";

    latestAnswerMarkdown = "";

    $("welcomeSection").classList.remove("hidden");

    $("quickPrompts").classList.remove("hidden");

    $("composerTitle").textContent =
        "Where do you want to go?";

    $("composerHint").textContent =
        "Example: Plan a complete 7 days Japan trip from Bangladesh under 2 lakhs.";

    $("btnText").textContent = "Generate Plan";

    $("userInput").placeholder =
        "Plan your next trip including flights, hotels and sightseeing...";

    hideError();
}


// =====================================================
// 9. ENTER CONVERSATION MODE
// =====================================================

function enterConversationMode() {

    $("welcomeSection").classList.add("hidden");

    $("quickPrompts").classList.add("hidden");

    $("conversationSection").classList.remove("hidden");

    $("composerTitle").textContent =
        "Continue your trip conversation";

    $("composerHint").textContent =
        "Ask a follow-up or change your itinerary, hotels, flights, or budget.";

    $("btnText").textContent = "Send Message";

    $("userInput").placeholder =
        "Ask a follow-up question about this trip...";
}


// =====================================================
// 10. APPEND USER OR ASSISTANT MESSAGE
// =====================================================

function appendMessage(role, content) {

    enterConversationMode();

    const wrapper = document.createElement("article");

    wrapper.className =
        `chat-message ${
            role === "user" ? "user" : "assistant"
        }`;

    const label = document.createElement("div");

    label.className = "message-role";

    label.textContent =
        role === "user" ? "You" : "TripMate AI";

    const body = document.createElement("div");

    body.className = "message-content";

    if (role === "assistant") {

        renderMarkdown(body, content);

    } else {

        body.textContent = content;

        body.style.whiteSpace = "pre-wrap";
    }

    wrapper.append(label, body);

    $("conversationMessages").appendChild(wrapper);

    return wrapper;
}


// =====================================================
// 11. RENDER FULL CONVERSATION
// =====================================================

function renderMessages(messages) {

    $("conversationMessages").replaceChildren();

    if (!messages.length) {

        clearConversationView();

        return;
    }

    for (const item of messages) {

        if (
            item.role === "user" ||
            item.role === "assistant"
        ) {

            appendMessage(
                item.role,
                String(item.content || "")
            );
        }
    }

    const lastAssistant = [...messages]
        .reverse()
        .find(
            (item) => item.role === "assistant"
        );

    showResult(
        lastAssistant
            ? String(lastAssistant.content || "")
            : "",
        currentThreadId
    );
}


// =====================================================
// 12. HIGHLIGHT SELECTED CHAT
// =====================================================

function highlightActiveChat() {

    const chatItems = $("chatList").querySelectorAll(
        ".chat-item"
    );

    for (const item of chatItems) {

        const active =
            item.dataset.threadId === currentThreadId;

        item.classList.toggle(
            "active",
            active
        );

        if (active) {

            item.setAttribute(
                "aria-current",
                "true"
            );

        } else {

            item.removeAttribute(
                "aria-current"
            );
        }
    }
}


// =====================================================
// 13. LOAD RECENT CHAT HISTORY
// =====================================================

async function loadChatList() {

    const data = await apiRequest("/api/chats");

    const list = $("chatList");

    list.replaceChildren();

    if (
        !Array.isArray(data.chats) ||
        data.chats.length === 0
    ) {

        const empty = document.createElement("p");

        empty.className = "empty-chat-list";

        empty.textContent =
            "No conversations yet.";

        list.appendChild(empty);

        return [];
    }

    for (const chat of data.chats) {

        const button = document.createElement("button");

        button.type = "button";

        button.className = "chat-item";

        button.dataset.threadId = chat.thread_id;

        const icon = document.createElement("span");

        icon.className = "chat-item-icon";

        icon.textContent = "💬";

        const title = document.createElement("span");

        title.className = "chat-title";

        title.textContent =
            chat.title || "New Trip";

        button.title = title.textContent;

        button.append(icon, title);

        button.addEventListener(
            "click",
            () => openChat(chat.thread_id)
        );

        list.appendChild(button);
    }

    highlightActiveChat();

    return data.chats;
}


// =====================================================
// 14. OPEN PREVIOUS CHAT
// =====================================================

async function openChat(threadId) {

    if (isSending) {
        return;
    }

    const version = ++navigationVersion;

    hideError();

    try {

        const data = await apiRequest(
            `/api/chats/${encodeURIComponent(threadId)}`
        );

        if (version !== navigationVersion) {
            return;
        }

        currentThreadId = data.thread_id;

        localStorage.setItem(
            "travel_thread_id",
            currentThreadId
        );

        renderMessages(
            Array.isArray(data.messages)
                ? data.messages
                : []
        );

        highlightActiveChat();

        closeSidebar();

    } catch (error) {

        if (version === navigationVersion) {

            showError(error.message);
        }
    }
}


// =====================================================
// 15. CREATE NEW CHAT
// =====================================================

async function newChat() {

    if (isSending) {
        return;
    }

    const version = ++navigationVersion;

    hideError();

    try {

        const data = await apiRequest(
            "/api/new-chat",
            {
                method: "POST"
            }
        );

        if (version !== navigationVersion) {
            return;
        }

        currentThreadId = data.thread_id;

        localStorage.setItem(
            "travel_thread_id",
            currentThreadId
        );

        clearConversationView();

        await loadChatList();

        closeSidebar();

        $("userInput").focus();

    } catch (error) {

        showError(error.message);
    }
}


// =====================================================
// 16. SEND TRAVEL REQUEST OR FOLLOW-UP
// =====================================================

async function sendMessage() {

    if (isSending) {
        return;
    }

    hideError();

    const input = $("userInput");

    const message = input.value.trim();

    if (!message) {

        showError(
            "Please enter your travel request first."
        );

        return;
    }

    setLoading(true);

    const sentThreadId = currentThreadId;

    const version = ++navigationVersion;

    try {

        const data = await apiRequest(
            "/api/travel",
            {
                method: "POST",

                headers: {
                    "Content-Type": "application/json"
                },

                body: JSON.stringify({
                    message: message,
                    thread_id: sentThreadId
                })
            }
        );

        if (version !== navigationVersion) {
            return;
        }

        currentThreadId = data.thread_id;

        localStorage.setItem(
            "travel_thread_id",
            currentThreadId
        );

        input.value = "";

        // Reload the conversation from the backend.
        // This prevents duplicate messages in the UI.

        const detail = await apiRequest(
            `/api/chats/${
                encodeURIComponent(currentThreadId)
            }`
        );

        if (version !== navigationVersion) {
            return;
        }

        renderMessages(
            detail.messages || []
        );

        showResult(
            String(data.answer || ""),
            currentThreadId
        );

        await loadChatList();

        $("conversationMessages")
            .lastElementChild
            ?.scrollIntoView({
                behavior: "smooth",
                block: "end"
            });

    } catch (error) {

        showError(error.message);

        // Keep the typed message if something fails.

        input.value = message;

        if (currentThreadId) {

            try {

                await loadChatList();

            } catch (_) {

                // Preserve the original error.
            }
        }

    } finally {

        setLoading(false);
    }
}


// =====================================================
// 17. COPY TRAVEL PLAN
// =====================================================

async function copyResult() {

    const text = $("resultBox").innerText;

    if (!text) {

        showError(
            "No travel plan available to copy."
        );

        return;
    }

    try {

        await navigator.clipboard.writeText(text);

        const button = document.querySelector(
            ".copy-btn"
        );

        const original = button.textContent;

        button.textContent = "Copied!";

        setTimeout(() => {

            button.textContent = original;

        }, 1400);

    } catch (_) {

        showError(
            "Could not copy result."
        );
    }
}


// =====================================================
// 18. DOWNLOAD TRAVEL PLAN AS PDF
// =====================================================

function downloadPDF() {

    const pdfContent = $("pdfContent");

    if (!latestAnswerMarkdown || !pdfContent) {

        showError(
            "No travel plan available to download."
        );

        return;
    }

    if (typeof html2pdf === "undefined") {

        showError(
            "PDF library did not load. Check your internet connection."
        );

        return;
    }

    const button = document.querySelector(
        ".download-btn"
    );

    const original = button.textContent;

    button.textContent = "Preparing PDF...";

    button.disabled = true;

    const options = {

        margin: 0.5,

        filename: "ai-travel-plan.pdf",

        image: {
            type: "jpeg",
            quality: 0.98
        },

        html2canvas: {
            scale: 2,
            useCORS: true,
            backgroundColor: "#ffffff"
        },

        jsPDF: {
            unit: "in",
            format: "a4",
            orientation: "portrait"
        },

        pagebreak: {
            mode: [
                "avoid-all",
                "css",
                "legacy"
            ]
        }
    };

    html2pdf()
        .set(options)
        .from(pdfContent)
        .save()
        .catch(() => {

            showError(
                "Could not download PDF."
            );

        })
        .finally(() => {

            button.textContent = original;

            button.disabled = false;
        });
}


// =====================================================
// 19. CLOSE MOBILE SIDEBAR
// =====================================================

function closeSidebar() {

    $("sidebar").classList.remove(
        "open",
        "sidebar-open"
    );
}


// =====================================================
// 20. RESTORE CHAT AFTER PAGE REFRESH
// =====================================================

async function initializeChat() {

    try {

        const chats = await loadChatList();

        const saved = localStorage.getItem(
            "travel_thread_id"
        );

        // Only open a saved conversation if
        // the backend says it belongs to this session.

        const chosen =
            chats.find(
                (chat) => chat.thread_id === saved
            ) || chats[0];

        if (chosen) {

            await openChat(
                chosen.thread_id
            );

        } else {

            currentThreadId = null;

            localStorage.removeItem(
                "travel_thread_id"
            );

            clearConversationView();
        }

    } catch (error) {

        showError(
            `Could not load chat history: ${error.message}`
        );
    }
}


// =====================================================
// 21. INITIALIZE EVENT LISTENERS
// =====================================================

document.addEventListener(
    "DOMContentLoaded",
    () => {

        // New Chat button

        $("newChatBtn").addEventListener(
            "click",
            newChat
        );

        // Open or close sidebar

        $("sidebarToggleBtn").addEventListener(
            "click",
            () => {

                $("sidebar").classList.toggle(
                    "open"
                );
            }
        );

        // Close sidebar button

        $("sidebarCloseBtn").addEventListener(
            "click",
            closeSidebar
        );

        // Ctrl + Enter / Command + Enter
        // sends a message.

        document.addEventListener(
            "keydown",
            (event) => {

                if (
                    (event.ctrlKey || event.metaKey) &&
                    event.key === "Enter"
                ) {

                    event.preventDefault();

                    sendMessage();
                }
            }
        );

        // Load existing conversations
        // when the page opens.

        initializeChat();
    }
);
