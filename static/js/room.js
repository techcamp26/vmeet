// Extract configuration variables from window.ZOOM_CONFIG
const { meetingId, userId, username, isHost, waitingRoomEnabled, wsScheme, csrfToken } = window.ZOOM_CONFIG;

let socket = null;
let localStream = null;
let screenStream = null;
let isMuted = false;
let isCameraOff = false;
let isScreenSharing = false;
let isLocked = false;

// WebRTC Peer Connections dictionary
// Key: peer's channel_name, Value: RTCPeerConnection object
const peerConnections = {};

// ICE Configuration (free public Google STUN servers)
const iceConfig = {
    iceServers: [
        { urls: 'stun:stun.l.google.com:19302' },
        { urls: 'stun:stun1.l.google.com:19302' },
        { urls: 'stun:stun2.l.google.com:19302' }
    ]
};

// Initialize meeting
document.addEventListener('DOMContentLoaded', () => {
    initLocalMedia()
        .then(() => {
            initWebSocket();
        })
        .catch(err => {
            console.error("Failed to access camera/mic:", err);
            showToast("Camera/Microphone access denied. You will join without media.", "error");
            initWebSocket(); // Proceed anyway
        });
});

// Capture camera and microphone
async function initLocalMedia() {
    // Check for Secure Context (WebRTC requirement in browsers)
    if (!window.isSecureContext && window.location.hostname !== 'localhost' && window.location.hostname !== '127.0.0.1') {
        showToast("WebRTC requires HTTPS or localhost. Please access via a secure connection.", "error");
    }

    try {
        // Try to get both video and audio
        localStream = await navigator.mediaDevices.getUserMedia({
            video: {
                width: { ideal: 640 },
                height: { ideal: 360 },
                frameRate: { ideal: 24 }
            },
            audio: true
        });
    } catch (err) {
        console.warn("Failed to get both video and audio. Trying video only...", err);
        try {
            // Try video only
            localStream = await navigator.mediaDevices.getUserMedia({
                video: {
                    width: { ideal: 640 },
                    height: { ideal: 360 },
                    frameRate: { ideal: 24 }
                },
                audio: false
            });
            showToast("Microphone access failed. Joined with camera only.", "warning");
        } catch (err2) {
            console.warn("Failed to get video only. Trying audio only...", err2);
            try {
                // Try audio only
                localStream = await navigator.mediaDevices.getUserMedia({
                    video: false,
                    audio: true
                });
                showToast("Camera access failed. Joined with microphone only.", "warning");
            } catch (err3) {
                // All attempts failed
                throw err3;
            }
        }
    }
    
    const localVideo = document.getElementById('local-video');
    if (localVideo) {
        localVideo.srcObject = localStream;
    }
}

// Connect to Signaling WebSockets Channel
function initWebSocket() {
    const wsUrl = `${wsScheme}${window.location.host}/ws/meeting/${meetingId}/`;
    socket = new WebSocket(wsUrl);

    socket.onopen = () => {
        console.log("Signaling WebSocket connected.");
    };

    socket.onmessage = async (e) => {
        const data = JSON.parse(e.data);
        console.log("WebSocket message received:", data.type);

        switch (data.type) {
            case 'waiting_state':
                // User is placed in the waiting room
                const waitingOverlay = document.getElementById('waiting-overlay');
                if (waitingOverlay) waitingOverlay.style.display = 'flex';
                break;

            case 'connection_success':
                // Successfully joined the active room
                const overlay = document.getElementById('waiting-overlay');
                if (overlay) overlay.style.display = 'none';
                
                showToast("Connected to meeting room!", "success");
                break;

            case 'peer_joined':
                // A new peer joins -> initiate WebRTC call (if not self)
                const peerChan = data.channel_name;
                const peerName = data.username;
                const peerId = data.user_id;
                
                // Establish connection. We are the initiator (send offer).
                await createPeerConnection(peerChan, peerName, peerId, true);
                
                // Add option to private chat dropdown
                updatePrivateChatDropdown();
                break;

            case 'peer_left':
                // Tear down peer connection
                closePeerConnection(data.channel_name);
                showToast(`${data.username} left the meeting`, "info");
                updatePrivateChatDropdown();
                break;

            case 'signal':
                // Handle relayed WebRTC signaling
                await handleSignalingMessage(data.sender_channel, data.sender_username, data.sender_id, data.payload);
                break;

            case 'chat_message':
                appendChatMessage(data);
                break;

            case 'peer_lists_update':
                // Host-only dashboard list refresh
                if (isHost) {
                    renderHostParticipantsLists(data.active_participants, data.waiting_participants);
                }
                break;

            case 'waiting_room_request':
                if (isHost) {
                    showWaitingRoomRequestToast(data.username, data.channel_name);
                    // Expand/Notify Waiting room list
                    document.getElementById('host-waiting-room-section').style.display = 'block';
                }
                break;

            case 'room_locked_status':
                isLocked = data.is_locked;
                updateLockBtnUI();
                break;

            case 'host_command':
                handleHostCommand(data.action);
                break;

            case 'denied':
                showToast("Your request to join was denied by the host.", "error");
                setTimeout(() => { window.location.href = '/'; }, 2000);
                break;

            default:
                break;
        }
    };

    socket.onerror = (err) => {
        console.error("Signaling error:", err);
    };

    socket.onclose = (e) => {
        console.log("WebSocket connection closed:", e.code);
        if (e.code === 4003) {
            showToast("Meeting is locked. Cannot join.", "error");
            setTimeout(() => { window.location.href = '/'; }, 2500);
        } else if (e.code === 4002) {
            showToast("Meeting not found or has ended.", "error");
            setTimeout(() => { window.location.href = '/'; }, 2500);
        }
    };
}

// --------------------- WebRTC Mesh Logic ---------------------

// Create a new RTCPeerConnection for a peer
async function createPeerConnection(peerChannelName, peerUsername, peerId, isInitiator) {
    if (peerConnections[peerChannelName]) {
        console.warn("Peer connection already exists for:", peerChannelName);
        return peerConnections[peerChannelName];
    }

    console.log(`Creating RTCPeerConnection for ${peerUsername} (Initiator: ${isInitiator})`);
    
    const pc = new RTCPeerConnection(iceConfig);
    peerConnections[peerChannelName] = pc;

    // Attach local media tracks to this connection
    if (localStream) {
        localStream.getTracks().forEach(track => {
            pc.addTrack(track, localStream);
        });
    }

    // Capture ice candidates and relay them via WebSocket
    pc.onicecandidate = (event) => {
        if (event.candidate) {
            sendSocketMessage({
                type: 'ice-candidate',
                target_channel: peerChannelName,
                candidate: event.candidate
            });
        }
    };

    // When remote track is received, mount a video element
    pc.ontrack = (event) => {
        console.log("Remote track received from:", peerUsername);
        createRemoteVideoElement(peerChannelName, peerUsername, peerId, event.streams[0]);
    };

    pc.onconnectionstatechange = () => {
        console.log(`Connection state with ${peerUsername}: ${pc.connectionState}`);
        if (pc.connectionState === 'disconnected' || pc.connectionState === 'failed') {
            closePeerConnection(peerChannelName);
        }
    };

    // If we are initiating the call, create and send an SDP offer
    if (isInitiator) {
        try {
            const offer = await pc.createOffer();
            await pc.setLocalDescription(offer);
            
            sendSocketMessage({
                type: 'sdp-offer',
                target_channel: peerChannelName,
                sdp: offer
            });
        } catch (err) {
            console.error("Error creating offer:", err);
        }
    }

    return pc;
}

// Handle incoming Signaling (Offers, Answers, ICE Candidates)
async function handleSignalingMessage(senderChannel, senderUsername, senderId, payload) {
    let pc = peerConnections[senderChannel];

    if (payload.type === 'sdp-offer') {
        // We received an offer -> create a connection and answer
        pc = await createPeerConnection(senderChannel, senderUsername, senderId, false);
        try {
            await pc.setRemoteDescription(new RTCSessionDescription(payload.sdp));
            const answer = await pc.createAnswer();
            await pc.setLocalDescription(answer);
            
            sendSocketMessage({
                type: 'sdp-answer',
                target_channel: senderChannel,
                sdp: answer
            });
        } catch (err) {
            console.error("Error processing SDP offer:", err);
        }
    } else if (payload.type === 'sdp-answer') {
        // We received an answer -> set remote description
        if (pc) {
            try {
                await pc.setRemoteDescription(new RTCSessionDescription(payload.sdp));
            } catch (err) {
                console.error("Error setting remote description:", err);
            }
        }
    } else if (payload.type === 'ice-candidate') {
        // We received an ICE candidate -> add it to connection
        if (pc && payload.candidate) {
            try {
                await pc.addIceCandidate(new RTCIceCandidate(payload.candidate));
            } catch (err) {
                console.error("Error adding remote ICE candidate:", err);
            }
        }
    }
}

// Close and remove peer connections
function closePeerConnection(peerChannelName) {
    if (peerConnections[peerChannelName]) {
        peerConnections[peerChannelName].close();
        delete peerConnections[peerChannelName];
    }
    
    // Remove the HTML video element matching this channel name
    const videoWrapper = document.getElementById(`video-wrapper-${peerChannelName}`);
    if (videoWrapper) {
        videoWrapper.remove();
        recalculateVideoLayout();
    }
}

// Helper to push json messages onto the socket
function sendSocketMessage(data) {
    if (socket && socket.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify(data));
    }
}

// --------------------- Dynamic UI Renderer ---------------------

// Mount remote video tag in grid
function createRemoteVideoElement(peerChannelName, peerUsername, peerId, stream) {
    const videoGrid = document.getElementById('video-grid');
    
    // Check if element already exists
    let videoWrapper = document.getElementById(`video-wrapper-${peerChannelName}`);
    if (!videoWrapper) {
        videoWrapper = document.createElement('div');
        videoWrapper.id = `video-wrapper-${peerChannelName}`;
        videoWrapper.className = 'video-wrapper';
        
        const video = document.createElement('video');
        video.id = `video-${peerChannelName}`;
        video.autoplay = true;
        video.playsinline = true;
        // Standard video stream contains audio, so it should not be muted
        video.srcObject = stream;
        
        // Check if stream has a screen share track (non-camera stream labels)
        const isScreen = stream.getVideoTracks().some(track => track.label.toLowerCase().includes('screen'));
        if (isScreen) {
            video.className = 'screen-share';
        }
        
        const label = document.createElement('div');
        label.className = 'video-label';
        label.innerHTML = `
            <i class="fa-solid fa-user"></i>
            <span>${peerUsername}</span>
        `;
        
        const statusIcons = document.createElement('div');
        statusIcons.className = 'video-status-icons';
        statusIcons.id = `status-icons-${peerChannelName}`;
        
        const micIcon = document.createElement('div');
        micIcon.className = 'status-icon-badge';
        micIcon.id = `mic-status-${peerChannelName}`;
        micIcon.style.display = 'none';
        micIcon.innerHTML = `<i class="fa-solid fa-microphone-slash"></i>`;
        statusIcons.appendChild(micIcon);
        
        const placeholder = document.createElement('div');
        placeholder.className = 'video-placeholder';
        placeholder.id = `placeholder-${peerChannelName}`;
        placeholder.style.display = 'none';
        // Random avatar background color
        placeholder.innerHTML = `
            <div class="avatar" style="background-color: #8b5cf6;">
                ${peerUsername.slice(0, 2).toUpperCase()}
            </div>
            <span style="font-size: 14px; font-weight: 500;">${peerUsername}</span>
        `;
        
        videoWrapper.appendChild(video);
        videoWrapper.appendChild(label);
        videoWrapper.appendChild(statusIcons);
        videoWrapper.appendChild(placeholder);
        videoGrid.appendChild(videoWrapper);
        
        recalculateVideoLayout();
    } else {
        // Update stream
        const video = document.getElementById(`video-${peerChannelName}`);
        if (video) video.srcObject = stream;
    }
}

// Adjust sizes for video grids
function recalculateVideoLayout() {
    const videoGrid = document.getElementById('video-grid');
    const wrappers = videoGrid.getElementsByClassName('video-wrapper');
    const count = wrappers.length;
    
    if (count === 1) {
        wrappers[0].style.maxWidth = '80%';
        wrappers[0].style.flexBasis = '80%';
    } else if (count === 2) {
        for (let w of wrappers) {
            w.style.maxWidth = '45%';
            w.style.flexBasis = '45%';
        }
    } else {
        for (let w of wrappers) {
            w.style.maxWidth = '30%';
            w.style.flexBasis = '30%';
        }
    }
}

// --------------------- Chat Operations ---------------------

// Send text chat on socket
function sendChatMessage() {
    const chatInput = document.getElementById('chat-input');
    const targetSelect = document.getElementById('chat-target');
    const message = chatInput.value.trim();
    
    if (!message) return;

    const targetVal = targetSelect.value; // 'everyone' or target username
    
    const msgPayload = {
        type: 'chat_message',
        message: message,
        recipient: targetVal === 'everyone' ? null : targetVal
    };
    
    sendSocketMessage(msgPayload);
    chatInput.value = '';
}

// Render message inside chat box
function appendChatMessage(data) {
    const messagesContainer = document.getElementById('chat-messages-container');
    const isSelf = data.sender === username;
    
    const bubble = document.createElement('div');
    bubble.className = `chat-bubble ${isSelf ? 'self' : ''} ${data.is_private ? 'private' : ''}`;
    
    let privateNotice = '';
    if (data.is_private) {
        privateNotice = isSelf ? ` <span style="color: var(--accent-warning); font-size: 10px;">(Private to ${data.recipient})</span>` 
                               : ` <span style="color: var(--accent-warning); font-size: 10px;">(Private to You)</span>`;
    }

    bubble.innerHTML = `
        <div class="chat-bubble-header">
            <strong>${data.sender}${privateNotice}</strong>
            <span>${data.timestamp}</span>
        </div>
        <div>${escapeHTML(data.message)}</div>
    `;
    
    messagesContainer.appendChild(bubble);
    messagesContainer.scrollTop = messagesContainer.scrollHeight;
}

// Update choices in the private message recipient selector dropdown
function updatePrivateChatDropdown() {
    const dropdown = document.getElementById('chat-target');
    const currentVal = dropdown.value;
    
    // Clear and restore standard target option
    dropdown.innerHTML = '<option value="everyone">Everyone</option>';
    
    // List other peers
    const activePeers = getActivePeersList();
    activePeers.forEach(peer => {
        if (peer.username !== username) {
            const opt = document.createElement('option');
            opt.value = peer.username;
            opt.textContent = `${peer.username}`;
            dropdown.appendChild(opt);
        }
    });
    
    // Maintain selection if target still present
    dropdown.value = currentVal;
}

// Extract unique peer profiles in the room
function getActivePeersList() {
    const list = [];
    const elements = document.getElementById('active-participants-list').getElementsByClassName('participant-row');
    
    // Iterate through dynamic peers list
    for (let el of elements) {
        const usernameSpan = el.querySelector('span');
        if (usernameSpan) {
            let name = usernameSpan.textContent.replace(' (You)', '').replace(' (Host)', '').trim();
            list.push({ username: name });
        }
    }
    return list;
}

// --------------------- Host Panel Management ---------------------

// Render participant items in drawer
function renderHostParticipantsLists(activeParticipants, waitingParticipants) {
    const activeListContainer = document.getElementById('active-participants-list');
    const waitingListContainer = document.getElementById('waiting-requests-list');
    const waitingSection = document.getElementById('host-waiting-room-section');
    const badge = document.getElementById('participant-count-badge');
    
    badge.textContent = activeParticipants.length;

    // 1. Render Waiting Room Requests
    if (waitingParticipants.length > 0) {
        waitingSection.style.display = 'block';
        waitingListContainer.innerHTML = '';
        
        waitingParticipants.forEach(peer => {
            const row = document.createElement('div');
            row.className = 'participant-row';
            row.innerHTML = `
                <div class="participant-meta">
                    <div class="avatar" style="width: 28px; height: 28px; font-size: 11px; background-color: #8b5cf6;">
                        ${peer.username.slice(0, 2).toUpperCase()}
                    </div>
                    <span style="font-size: 13px;">${peer.username}</span>
                </div>
                <div class="participant-actions">
                    <button class="btn btn-success" style="padding: 4px 8px; font-size: 11px;" onclick="admitParticipant('${peer.channel_name}')">Admit</button>
                    <button class="btn btn-danger" style="padding: 4px 8px; font-size: 11px;" onclick="denyParticipant('${peer.channel_name}')">Deny</button>
                </div>
            `;
            waitingListContainer.appendChild(row);
        });
    } else {
        waitingSection.style.display = 'none';
    }

    // 2. Render Active Participants
    activeListContainer.innerHTML = '';
    activeParticipants.forEach(peer => {
        const isSelf = peer.user_id === userId;
        const row = document.createElement('div');
        row.className = 'participant-row';
        
        let labelSuffix = '';
        if (isSelf) labelSuffix = ' (You)';
        else if (peer.role === 'host') labelSuffix = ' (Host)';
        
        let hostControls = '';
        // If we are host, show kick/mute options next to non-hosts
        if (isHost && !isSelf) {
            hostControls = `
                <div class="participant-actions" style="margin-left: 10px;">
                    <button class="participant-btn" onclick="hostMutePeer(${peer.user_id})" title="Mute Mic"><i class="fa-solid fa-microphone-slash"></i></button>
                    <button class="participant-btn" onclick="hostDisableVideoPeer(${peer.user_id})" title="Disable Video"><i class="fa-solid fa-video-slash"></i></button>
                    <button class="participant-btn" onclick="hostKickPeer(${peer.user_id})" title="Remove"><i class="fa-solid fa-user-xmark" style="color: var(--accent-danger);"></i></button>
                </div>
            `;
        }

        row.innerHTML = `
            <div class="participant-meta">
                <div class="avatar" style="width: 28px; height: 28px; font-size: 11px; background-color: #3b82f6;">
                    ${peer.username.slice(0, 2).toUpperCase()}
                </div>
                <span style="font-size: 13px;"><strong>${peer.username}</strong>${labelSuffix}</span>
            </div>
            <div style="display: flex; align-items: center; gap: 8px;">
                <i class="fa-solid fa-microphone" id="status-mic-${peer.user_id}"></i>
                <i class="fa-solid fa-video" id="status-vid-${peer.user_id}"></i>
                ${hostControls}
            </div>
        `;
        activeListContainer.appendChild(row);
    });
}

// Host controls execution triggers
function admitParticipant(targetChannel) {
    sendSocketMessage({
        type: 'admit-participant',
        target_channel: targetChannel
    });
}

function denyParticipant(targetChannel) {
    sendSocketMessage({
        type: 'deny-participant',
        target_channel: targetChannel
    });
}

function hostMutePeer(targetUserId) {
    sendSocketMessage({
        type: 'mute-participant',
        user_id: targetUserId
    });
    showToast("Sent mute command to participant.", "info");
}

function hostDisableVideoPeer(targetUserId) {
    sendSocketMessage({
        type: 'disable-participant-video',
        user_id: targetUserId
    });
    showToast("Sent camera disable command to participant.", "info");
}

function hostKickPeer(targetUserId) {
    if (confirm("Are you sure you want to remove this participant?")) {
        sendSocketMessage({
            type: 'remove-participant',
            user_id: targetUserId
        });
    }
}

// --------------------- Host Commands Receiver ---------------------

function handleHostCommand(action) {
    switch (action) {
        case 'mute_audio':
            if (localStream && !isMuted) {
                toggleMuteAudio();
                showToast("You were muted by the host.", "info");
            }
            break;

        case 'disable_video':
            if (localStream && !isCameraOff) {
                toggleDisableVideo();
                showToast("Your camera was disabled by the host.", "info");
            }
            break;

        case 'kick':
            showToast("You have been removed from the meeting by the host.", "error");
            setTimeout(() => { window.location.href = '/'; }, 2000);
            break;

        case 'end_meeting':
            showToast("The host has ended this meeting for everyone.", "error");
            setTimeout(() => { window.location.href = '/'; }, 2500);
            break;

        default:
            break;
    }
}

// --------------------- Button Actions & Toggles ---------------------

// Toggle Mic Track State
function toggleMuteAudio() {
    if (!localStream) return;
    
    isMuted = !isMuted;
    localStream.getAudioTracks().forEach(track => {
        track.enabled = !isMuted;
    });
    
    const btn = document.getElementById('mic-btn');
    const selfLabel = document.getElementById('self-mic-status-label');
    const localIcon = document.getElementById('local-mic-status-icon');
    
    if (isMuted) {
        btn.classList.add('active');
        btn.innerHTML = '<i class="fa-solid fa-microphone-slash"></i>';
        if (selfLabel) selfLabel.className = 'fa-solid fa-microphone-slash';
        if (localIcon) localIcon.style.display = 'flex';
        showToast("Microphone muted", "info");
    } else {
        btn.classList.remove('active');
        btn.innerHTML = '<i class="fa-solid fa-microphone"></i>';
        if (selfLabel) selfLabel.className = 'fa-solid fa-microphone';
        if (localIcon) localIcon.style.display = 'none';
        showToast("Microphone unmuted", "info");
    }
}

// Toggle Camera Track State
function toggleDisableVideo() {
    if (!localStream) return;
    
    isCameraOff = !isCameraOff;
    localStream.getVideoTracks().forEach(track => {
        track.enabled = !isCameraOff;
    });
    
    const btn = document.getElementById('camera-btn');
    const selfLabel = document.getElementById('self-video-status-label');
    const placeholder = document.getElementById('local-video-placeholder');
    
    if (isCameraOff) {
        btn.classList.add('active');
        btn.innerHTML = '<i class="fa-solid fa-video-slash"></i>';
        if (selfLabel) selfLabel.className = 'fa-solid fa-video-slash';
        if (placeholder) placeholder.style.display = 'flex';
        showToast("Camera disabled", "info");
    } else {
        btn.classList.remove('active');
        btn.innerHTML = '<i class="fa-solid fa-video"></i>';
        if (selfLabel) selfLabel.className = 'fa-solid fa-video';
        if (placeholder) placeholder.style.display = 'none';
        showToast("Camera enabled", "info");
    }
}

// Toggle Screen Sharing Mode
async function toggleScreenShare() {
    const btn = document.getElementById('screen-btn');
    
    if (!isScreenSharing) {
        try {
            // Request Screen Media
            screenStream = await navigator.mediaDevices.getDisplayMedia({
                video: {
                    width: 1280,
                    height: 720,
                    frameRate: 15
                },
                audio: false
            });
            
            isScreenSharing = true;
            btn.classList.add('active');
            btn.style.backgroundColor = 'var(--accent-success)';
            btn.style.borderColor = 'var(--accent-success)';
            
            const screenTrack = screenStream.getVideoTracks()[0];
            
            // Switch track on all WebRTC peer connections (Mesh replacement)
            for (let ch in peerConnections) {
                const pc = peerConnections[ch];
                const senders = pc.getSenders();
                const videoSender = senders.find(sender => sender.track && sender.track.kind === 'video');
                if (videoSender) {
                    await videoSender.replaceTrack(screenTrack);
                }
            }
            
            // Render locally
            const localVideo = document.getElementById('local-video');
            if (localVideo) {
                localVideo.srcObject = screenStream;
                localVideo.className = 'screen-share'; // Center contain styling
            }
            
            // Revert back when sharing ends via native browser button
            screenTrack.onended = () => {
                stopScreenShare();
            };
            
            showToast("Sharing your screen...", "success");
        } catch (err) {
            console.error("Failed to share screen:", err);
            showToast("Screen sharing was cancelled or failed.", "error");
        }
    } else {
        stopScreenShare();
    }
}

// Stop sharing screen and revert to web camera
async function stopScreenShare() {
    if (!isScreenSharing) return;
    
    isScreenSharing = false;
    
    // Stop tracks
    if (screenStream) {
        screenStream.getTracks().forEach(track => track.stop());
        screenStream = null;
    }
    
    const btn = document.getElementById('screen-btn');
    btn.classList.remove('active');
    btn.style.backgroundColor = '';
    btn.style.borderColor = '';
    
    // Restore video element mirrors
    const localVideo = document.getElementById('local-video');
    if (localVideo) {
        localVideo.srcObject = localStream;
        localVideo.className = '';
    }
    
    // Revert track on peer connections
    if (localStream) {
        const cameraTrack = localStream.getVideoTracks()[0];
        for (let ch in peerConnections) {
            const pc = peerConnections[ch];
            const senders = pc.getSenders();
            const videoSender = senders.find(sender => sender.track && sender.track.kind === 'video');
            if (videoSender && cameraTrack) {
                await videoSender.replaceTrack(cameraTrack);
            }
        }
    }
    
    showToast("Screen sharing stopped", "info");
}

// Toggle Lock Status
function toggleMeetingLock() {
    isLocked = !isLocked;
    sendSocketMessage({
        type: 'lock-meeting',
        is_locked: isLocked
    });
}

function updateLockBtnUI() {
    const btn = document.getElementById('lock-btn');
    if (!btn) return;
    
    if (isLocked) {
        btn.classList.add('active');
        btn.innerHTML = '<i class="fa-solid fa-lock" style="color: var(--accent-warning);"></i>';
        showToast("Meeting locked! New participants cannot join.", "warning");
    } else {
        btn.classList.remove('active');
        btn.innerHTML = '<i class="fa-solid fa-unlock"></i>';
        showToast("Meeting unlocked.", "success");
    }
}

// Leave call and redirect to dashboard
function leaveMeeting() {
    if (confirm("Are you sure you want to leave this call?")) {
        cleanupCall();
        window.location.href = '/';
    }
}

// Host ends meeting for everyone
function endMeetingForAll() {
    if (confirm("Are you sure you want to end this meeting for everyone?")) {
        sendSocketMessage({
            type: 'end-meeting'
        });
        cleanupCall();
        window.location.href = '/';
    }
}

// Close cameras/mics and socket sessions
function cleanupCall() {
    if (localStream) {
        localStream.getTracks().forEach(track => track.stop());
    }
    if (screenStream) {
        screenStream.getTracks().forEach(track => track.stop());
    }
    for (let ch in peerConnections) {
        peerConnections[ch].close();
    }
    if (socket) {
        socket.close();
    }
}

// --------------------- UI Helper Utilities ---------------------

// Room drawer toggle
function toggleRoomSidebar() {
    const sidebar = document.getElementById('room-sidebar');
    const toggleBtn = document.getElementById('sidebar-toggle-btn');
    const participantsBtn = document.getElementById('participants-btn');
    
    if (sidebar) sidebar.classList.toggle('active');
    if (toggleBtn) toggleBtn.classList.toggle('active');
    
    // Sync participants button active class
    if (sidebar) {
        const isSidebarActive = sidebar.classList.contains('active');
        const participantsTabBtn = document.getElementById('participants-tab-btn');
        const isParticipantsTabActive = participantsTabBtn ? participantsTabBtn.classList.contains('active') : false;
        if (isSidebarActive && isParticipantsTabActive) {
            if (participantsBtn) participantsBtn.classList.add('active');
        } else {
            if (participantsBtn) participantsBtn.classList.remove('active');
        }
    }
}

// Tab changes in drawer
function toggleSidebarTab(tabName) {
    const chatTabBtn = document.getElementById('chat-tab-btn');
    const participantsTabBtn = document.getElementById('participants-tab-btn');
    const chatPanel = document.getElementById('chat-panel');
    const participantsPanel = document.getElementById('participants-panel');
    const participantsBtn = document.getElementById('participants-btn');
    const sidebar = document.getElementById('room-sidebar');
    
    if (tabName === 'chat') {
        if (chatTabBtn) chatTabBtn.classList.add('active');
        if (participantsTabBtn) participantsTabBtn.classList.remove('active');
        if (chatPanel) chatPanel.classList.add('active');
        if (participantsPanel) participantsPanel.classList.remove('active');
        
        if (participantsBtn) participantsBtn.classList.remove('active');
    } else {
        if (chatTabBtn) chatTabBtn.classList.remove('active');
        if (participantsTabBtn) participantsTabBtn.classList.add('active');
        if (chatPanel) chatPanel.classList.remove('active');
        if (participantsPanel) participantsPanel.classList.add('active');
        
        // If sidebar is open, highlight the participants btn
        if (sidebar && sidebar.classList.contains('active')) {
            if (participantsBtn) participantsBtn.classList.add('active');
        }
    }
}

// Toggle Participants List view from the main control bar
function toggleParticipantsList() {
    const sidebar = document.getElementById('room-sidebar');
    if (!sidebar) return;
    
    const participantsTabBtn = document.getElementById('participants-tab-btn');
    const isSidebarActive = sidebar.classList.contains('active');
    const isParticipantsTabActive = participantsTabBtn ? participantsTabBtn.classList.contains('active') : false;
    
    if (!isSidebarActive) {
        toggleRoomSidebar();
        toggleSidebarTab('participants');
    } else {
        if (isParticipantsTabActive) {
            toggleRoomSidebar();
        } else {
            toggleSidebarTab('participants');
        }
    }
}

// Render dynamic in-app alerts (toasts)
function showToast(message, type = 'info') {
    const container = document.getElementById('alerts-container');
    if (!container) return;
    
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    
    let icon = 'fa-circle-info';
    if (type === 'success') icon = 'fa-circle-check';
    if (type === 'error') icon = 'fa-circle-exclamation';
    if (type === 'warning') icon = 'fa-triangle-exclamation';
    
    toast.innerHTML = `
        <i class="fa-solid ${icon}"></i>
        <span>${message}</span>
    `;
    container.appendChild(toast);
    
    // slide out and delete
    setTimeout(() => {
        toast.style.transform = 'translateX(120%)';
        toast.style.opacity = '0';
        toast.style.transition = 'all 0.5s ease';
        setTimeout(() => toast.remove(), 500);
    }, 4000);
}

// Sanitize inputs
function escapeHTML(str) {
    return str.replace(/[&<>'"]/g, 
        tag => ({
            '&': '&amp;',
            '<': '&lt;',
            '>': '&gt;',
            "'": '&#39;',
            '"': '&quot;'
        }[tag] || tag)
    );
}

// Show special toast notification for waiting room requests with Admit/Deny actions
function showWaitingRoomRequestToast(username, channelName) {
    const container = document.getElementById('alerts-container');
    if (!container) return;
    
    const toast = document.createElement('div');
    toast.className = 'toast';
    toast.style.background = 'var(--bg-secondary)';
    toast.style.border = '1px solid var(--accent-warning)';
    toast.style.flexDirection = 'column';
    toast.style.alignItems = 'stretch';
    toast.style.gap = '10px';
    toast.style.minWidth = '280px';
    toast.style.boxShadow = '0 10px 25px rgba(0, 0, 0, 0.5)';
    
    toast.innerHTML = `
        <div style="display: flex; align-items: center; gap: 10px; color: var(--text-primary);">
            <i class="fa-solid fa-circle-exclamation" style="color: var(--accent-warning); font-size: 16px;"></i>
            <span><strong>${username}</strong> is waiting to join.</span>
        </div>
        <div style="display: flex; gap: 8px; justify-content: flex-end; margin-top: 4px;">
            <button class="btn btn-success" style="padding: 6px 12px; font-size: 12px; border-radius: 6px; font-weight: 600;" onclick="admitParticipant('${channelName}'); this.closest('.toast').remove();">Admit</button>
            <button class="btn btn-danger" style="padding: 6px 12px; font-size: 12px; border-radius: 6px; font-weight: 600;" onclick="denyParticipant('${channelName}'); this.closest('.toast').remove();">Deny</button>
        </div>
    `;
    
    container.appendChild(toast);
    
    // Automatically fade and remove after 15 seconds if no action is taken
    setTimeout(() => {
        if (toast.parentNode) {
            toast.style.transform = 'translateX(120%)';
            toast.style.opacity = '0';
            toast.style.transition = 'all 0.5s ease';
            setTimeout(() => toast.remove(), 500);
        }
    }, 15000);
}

// Meeting Recording variables
let mediaRecorder = null;
let recordedChunks = [];
let isRecording = false;
let recordingStream = null;

// Toggle client-side screen recording
async function toggleRecording() {
    const btn = document.getElementById('record-btn');
    if (!btn) return;
    
    if (!isRecording) {
        try {
            // Prompt host/participant to select screen/window/tab with audio sharing
            recordingStream = await navigator.mediaDevices.getDisplayMedia({
                video: { frameRate: 30 },
                audio: true
            });
            
            recordedChunks = [];
            
            // Set up MediaRecorder options (VP9/VP8/Default container)
            let options = { mimeType: 'video/webm;codecs=vp9,opus' };
            if (!MediaRecorder.isTypeSupported(options.mimeType)) {
                options = { mimeType: 'video/webm;codecs=vp8,opus' };
                if (!MediaRecorder.isTypeSupported(options.mimeType)) {
                    options = { mimeType: 'video/webm' };
                }
            }
            
            mediaRecorder = new MediaRecorder(recordingStream, options);
            
            mediaRecorder.ondataavailable = (event) => {
                if (event.data && event.data.size > 0) {
                    recordedChunks.push(event.data);
                }
            };
            
            mediaRecorder.onstop = () => {
                // Generate and download the recorded webm file
                const blob = new Blob(recordedChunks, { type: 'video/webm' });
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.style.display = 'none';
                a.href = url;
                a.download = `vzoom-meeting-${meetingId}-${new Date().toISOString().slice(0, 10)}.webm`;
                document.body.appendChild(a);
                a.click();
                setTimeout(() => {
                    document.body.removeChild(a);
                    window.URL.revokeObjectURL(url);
                }, 100);
                
                // Reset recording button UI state
                btn.classList.remove('active');
                btn.style.backgroundColor = '';
                btn.innerHTML = '<i class="fa-solid fa-circle" style="color: #ef4444;"></i>';
                btn.title = "Record Meeting";
                isRecording = false;
                showToast("Meeting recording saved and downloaded successfully!", "success");
                
                // Stop capturing resources
                if (recordingStream) {
                    recordingStream.getTracks().forEach(track => track.stop());
                    recordingStream = null;
                }
            };
            
            mediaRecorder.start();
            isRecording = true;
            btn.classList.add('active');
            btn.style.backgroundColor = 'var(--accent-danger)';
            btn.innerHTML = '<i class="fa-solid fa-stop" style="color: #ffffff;"></i>';
            btn.title = "Stop Recording";
            showToast("Meeting recording started...", "success");
            
            // Handle cases where the user clicks "Stop sharing" from the browser overlay
            recordingStream.getVideoTracks()[0].onended = () => {
                if (isRecording) {
                    mediaRecorder.stop();
                }
            };
            
        } catch (err) {
            console.error("Failed to start recording:", err);
            showToast("Could not start recording. Please grant screen & audio permissions.", "error");
        }
    } else {
        if (mediaRecorder && mediaRecorder.state !== 'inactive') {
            mediaRecorder.stop();
        }
    }
}
