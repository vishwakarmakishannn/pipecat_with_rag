import React, { lazy, Suspense, useCallback, useMemo, useState } from 'react';
import { RTVIEvent, PipecatClient } from '@pipecat-ai/client-js';
import {
  PipecatClientAudio,
  PipecatClientProvider,
  usePipecatClient,
  usePipecatClientMicControl,
  usePipecatClientTransportState,
  useRTVIClientEvent,
  VoiceVisualizer,
} from '@pipecat-ai/client-react';
import { SmallWebRTCTransport } from '@pipecat-ai/small-webrtc-transport';
import { Mic, Volume2, X } from 'lucide-react';
import { jwtDecode } from 'jwt-decode';
import { fetchWithAuth, API_BASE } from './utils/api';
import './App.css';

const START_ENDPOINT =
  import.meta.env.VITE_PIPECAT_START_URL ||
  `${API_BASE}/start`;

const Auth = lazy(() => import('./components/Auth'));
const Sidebar = lazy(() => import('./components/Sidebar'));
const TranscriptPanel = lazy(() => import('./components/TranscriptPanel'));
const ChunkInspector = lazy(() => import('./components/ChunkInspector'));

function createPipecatClient() {
  return new PipecatClient({
    transport: new SmallWebRTCTransport(),
    enableMic: true,
    enableCam: false,
  });
}

function isValidTokenValue(token) {
  if (!token) return false;
  try {
    const decoded = jwtDecode(token);
    return decoded.exp * 1000 > Date.now();
  } catch {
    return false;
  }
}

function VoiceApp({ onResetClient }) {
  const pcClient = usePipecatClient();
  const transportState = usePipecatClientTransportState();
  const { enableMic, isMicEnabled } = usePipecatClientMicControl();
  
  const [isConnecting, setIsConnecting] = useState(false);
  const [error, setError] = useState('');
  
  const [transcripts, setTranscripts] = useState([]);
  const [conversations, setConversations] = useState([]);
  const [currentConversationId, setCurrentConversationId] = useState(null);
  
  const [memories, setMemories] = useState([]);
  const [isMemoryPanelOpen, setIsMemoryPanelOpen] = useState(false);
  const [isMemoryLoading, setIsMemoryLoading] = useState(false);
  const [memoryError, setMemoryError] = useState('');
  
  const [sidebarTab, setSidebarTab] = useState('chats');
  const [ragFiles, setRagFiles] = useState([]);
  const [isFilesLoading, setIsFilesLoading] = useState(false);
  const [isUploadingFile, setIsUploadingFile] = useState(false);
  const [isAddingLink, setIsAddingLink] = useState(false);
  const [linkUrl, setLinkUrl] = useState('');
  const [fileError, setFileError] = useState('');
  const [inspectedFile, setInspectedFile] = useState(null);
  
  const currentConversationIdRef = React.useRef(null);
  const transcriptAreaRef = React.useRef(null);
  const transcriptBottomRef = React.useRef(null);
  const shouldAutoScrollRef = React.useRef(true);
  
  const botTextRef = React.useRef('');
  const activeBotMessageIdRef = React.useRef(null);
  const toolCallPayloadsRef = React.useRef({});
  const savedToolCallIdsRef = React.useRef(new Set());
  
  const [expandedToolCalls, setExpandedToolCalls] = useState({});
  const [latencyStats, setLatencyStats] = useState([]);

  const toggleToolCall = (id) => {
    setExpandedToolCalls(prev => ({...prev, [id]: !prev[id]}));
  };

  const isActive = ['connected', 'ready'].includes(transportState);
  const canStart = ['disconnected', 'initialized', 'error'].includes(transportState);

  const statusLabel = useMemo(() => {
    if (isConnecting) return 'Connecting';
    if (transportState === 'ready') return 'Connected';
    return transportState.charAt(0).toUpperCase() + transportState.slice(1);
  }, [isConnecting, transportState]);

  const fetchConversations = useCallback(async () => {
    try {
      const res = await fetchWithAuth(`/api/conversations`);
      if (res.ok) setConversations(await res.json());
    } catch (err) {
      console.warn('Failed to fetch conversations', err);
    }
  }, []);

  const fetchMemories = useCallback(async () => {
    setIsMemoryLoading(true);
    setMemoryError('');
    try {
      const res = await fetchWithAuth(`/api/memories`);
      if (!res.ok) throw new Error('Could not load memories');
      setMemories(await res.json());
    } catch (err) {
      setMemoryError(err?.message || 'Could not load memories');
    } finally {
      setIsMemoryLoading(false);
    }
  }, []);

  const fetchFiles = useCallback(async () => {
    setIsFilesLoading(true);
    setFileError('');
    try {
      const res = await fetchWithAuth(`/api/files`);
      if (!res.ok) throw new Error('Could not load files');
      setRagFiles(await res.json());
    } catch (err) {
      setFileError(err?.message || 'Could not load files');
    } finally {
      setIsFilesLoading(false);
    }
  }, []);

  React.useEffect(() => {
    const timeoutId = window.setTimeout(() => {
      fetchConversations();
      fetchFiles();
    }, 0);
    return () => window.clearTimeout(timeoutId);
  }, [fetchConversations, fetchFiles]);

  React.useEffect(() => {
    if (sidebarTab !== 'files') return undefined;
    if (!ragFiles.some((file) => file.status === 'processing')) return undefined;
    const intervalId = window.setInterval(fetchFiles, 2500);
    return () => window.clearInterval(intervalId);
  }, [fetchFiles, ragFiles, sidebarTab]);

  React.useEffect(() => {
    currentConversationIdRef.current = currentConversationId;
  }, [currentConversationId]);

  React.useEffect(() => {
    if (!shouldAutoScrollRef.current) return;
    transcriptBottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
  }, [transcripts]);

  const handleTranscriptScroll = useCallback(() => {
    const area = transcriptAreaRef.current;
    if (!area) return;
    const distanceFromBottom = area.scrollHeight - area.scrollTop - area.clientHeight;
    shouldAutoScrollRef.current = distanceFromBottom < 80;
  }, []);

  const loadConversation = async (id) => {
    if (isActive) await disconnect();
    currentConversationIdRef.current = id;
    setCurrentConversationId(id);
    try {
      const res = await fetchWithAuth(`/api/conversations/${id}/messages`);
      if (res.ok) {
        const msgs = await res.json();
        setTranscripts(msgs.map(m => ({
          id: m.id,
          role: m.role,
          text: m.content,
          timestamp: new Date(m.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
        })));
      }
    } catch (err) {
      console.warn('Failed to load conversation', err);
    }
  };

  const deleteConversation = async (e, id) => {
    e.stopPropagation();
    try {
      const res = await fetchWithAuth(`/api/conversations/${id}`, {
        method: 'DELETE'
      });
      if (res.ok) {
        if (currentConversationId === id) {
          currentConversationIdRef.current = null;
          setCurrentConversationId(null);
          setTranscripts([]);
        }
        fetchConversations();
      }
    } catch (err) {
      console.warn('Failed to delete conversation', err);
    }
  };

  const toggleMemoryPanel = () => {
    const nextOpen = !isMemoryPanelOpen;
    setIsMemoryPanelOpen(nextOpen);
    if (nextOpen) fetchMemories();
  };

  const deleteMemory = async (id) => {
    try {
      const res = await fetchWithAuth(`/api/memories/${id}`, {
        method: 'DELETE'
      });
      if (!res.ok) throw new Error('Could not delete memory');
      setMemories((items) => items.filter((memory) => memory.id !== id));
    } catch (err) {
      setMemoryError(err?.message || 'Could not delete memory');
    }
  };

  const deleteAllMemories = async () => {
    try {
      const res = await fetchWithAuth(`/api/memories`, {
        method: 'DELETE'
      });
      if (!res.ok) throw new Error('Could not delete memories');
      setMemories([]);
    } catch (err) {
      setMemoryError(err?.message || 'Could not delete memories');
    }
  };

  const uploadRagFile = async (event) => {
    const file = event.target.files?.[0];
    event.target.value = '';
    if (!file) return;

    setIsUploadingFile(true);
    setFileError('');
    try {
      const formData = new FormData();
      formData.append('file', file);
      const res = await fetchWithAuth(`/api/files`, {
        method: 'POST',
        body: formData,
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || 'Could not upload PDF');
      }
      await fetchFiles();
    } catch (err) {
      setFileError(err?.message || 'Could not upload PDF');
    } finally {
      setIsUploadingFile(false);
    }
  };

  const addRagLink = async (event) => {
    event.preventDefault();
    const url = linkUrl.trim();
    if (!url) return;

    setIsAddingLink(true);
    setFileError('');
    try {
      const res = await fetchWithAuth(`/api/files/link`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({ url })
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || 'Could not add link');
      }
      setLinkUrl('');
      await fetchFiles();
    } catch (err) {
      setFileError(err?.message || 'Could not add link');
    } finally {
      setIsAddingLink(false);
    }
  };

  const deleteRagFile = async (id) => {
    try {
      const res = await fetchWithAuth(`/api/files/${id}`, {
        method: 'DELETE'
      });
      if (!res.ok) throw new Error('Could not delete file');
      setRagFiles((items) => items.filter((file) => file.id !== id));
    } catch (err) {
      setFileError(err?.message || 'Could not delete file');
    }
  };

  const startNewConversation = async () => {
    if (isActive) await disconnect();
    currentConversationIdRef.current = null;
    setCurrentConversationId(null);
    setTranscripts([]);
    setLatencyStats([]);
  };

  const saveToolCallTranscript = useCallback(async (payload) => {
    const convId = currentConversationIdRef.current;
    if (!convId) return;

    try {
      const res = await fetchWithAuth(`/api/conversations/${convId}/messages`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({
          role: 'ToolCall',
          content: JSON.stringify(payload)
        })
      });
      if (res.ok) fetchConversations();
    } catch (err) {
      console.warn('Failed to save tool call transcript', err);
    }
  }, [fetchConversations]);

  const addTranscript = useCallback((role, text, isDelta = false, messageId = null) => {
    if (!text) return;
    
    setTranscripts((items) => {
      const existingIndex = messageId ? items.findIndex(item => item.id === messageId) : -1;
      if (existingIndex !== -1 && isDelta) {
        const updated = [...items];
        updated[existingIndex] = {
          ...updated[existingIndex],
          text: updated[existingIndex].text + text,
        };
        return updated;
      }
      
      return [
        ...items,
        {
          id: messageId || `${Date.now()}-${items.length}`,
          role,
          text,
          timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }),
        },
      ];
    });
  }, []);

  useRTVIClientEvent(
    RTVIEvent.TransportStateChanged,
    useCallback((state) => {
      if (state === 'ready' || state === 'connected' || state === 'error') {
        setIsConnecting(false);
      }
    }, []),
  );

  useRTVIClientEvent(
    RTVIEvent.UserTranscript,
    useCallback((data) => {
      if (data.final) addTranscript('You', data.text, false);
    }, [addTranscript]),
  );

  useRTVIClientEvent(
    RTVIEvent.BotLlmStarted,
    useCallback(() => {
      botTextRef.current = '';
      activeBotMessageIdRef.current = `bot-${Date.now()}`;
    }, []),
  );

  useRTVIClientEvent(
    RTVIEvent.LLMFunctionCallInProgress,
    useCallback((data) => {
      const toolCallId = data.tool_call_id || `tool-${Date.now()}`;
      const payload = {
        tool_call_id: toolCallId,
        function_name: data.function_name,
        arguments: data.arguments || data.args,
        result: undefined
      };
      toolCallPayloadsRef.current[toolCallId] = payload;

      setTranscripts((items) => {
        const existingIdx = items.findIndex(i => i.id === toolCallId);
        if (existingIdx !== -1) {
          const updated = [...items];
          updated[existingIdx] = {
            ...updated[existingIdx],
            text: JSON.stringify(payload)
          };
          return updated;
        }
        return [
          ...items,
          {
            id: toolCallId,
            role: 'ToolCall',
            text: JSON.stringify(payload),
            timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }),
          }
        ];
      });
    }, []),
  );

  useRTVIClientEvent(
    RTVIEvent.LLMFunctionCallStopped,
    useCallback((data) => {
      const toolCallId = data.tool_call_id || `tool-${Date.now()}`;
      const previousPayload = toolCallPayloadsRef.current[toolCallId] || {};
      const payloadToSave = {
        tool_call_id: toolCallId,
        function_name: data.function_name || previousPayload.function_name,
        arguments: data.arguments || data.args || previousPayload.arguments,
        result: data.result
      };
      toolCallPayloadsRef.current[toolCallId] = payloadToSave;

      setTranscripts((items) => {
        const existingIdx = items.findIndex(i => i.id === toolCallId);
        if (existingIdx !== -1) {
          const updated = [...items];
          updated[existingIdx] = {
            ...updated[existingIdx],
            text: JSON.stringify(payloadToSave)
          };
          return updated;
        }
        return [
          ...items,
          {
            id: toolCallId,
            role: 'ToolCall',
            text: JSON.stringify(payloadToSave),
            timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }),
          }
        ];
      });
      if (!savedToolCallIdsRef.current.has(toolCallId)) {
        savedToolCallIdsRef.current.add(toolCallId);
        saveToolCallTranscript(payloadToSave);
      }
    }, [saveToolCallTranscript]),
  );

  useRTVIClientEvent(
    RTVIEvent.BotLlmText,
    useCallback((data) => {
      botTextRef.current += data.text;
      if (!activeBotMessageIdRef.current) {
        activeBotMessageIdRef.current = `bot-${Date.now()}`;
      }
      addTranscript('Aura', data.text, true, activeBotMessageIdRef.current);
    }, [addTranscript]),
  );

  useRTVIClientEvent(
    RTVIEvent.BotLlmStopped,
    useCallback(() => {
      activeBotMessageIdRef.current = null;
      fetchConversations();
    }, [fetchConversations]),
  );

  useRTVIClientEvent(
    RTVIEvent.Error,
    useCallback((message) => {
      setError(message?.data?.error || message?.data?.message || 'Pipecat connection failed');
      setIsConnecting(false);
    }, []),
  );

  useRTVIClientEvent(
    RTVIEvent.ServerMessage,
    useCallback((data) => {
      const messageData = data?.data || data;
      if (messageData?.type === 'latency_stats' && messageData.payload) {
        setLatencyStats((items) => [...items.slice(-19), { ...messageData.payload, receivedAt: Date.now() }]);
        return;
      }
      if (messageData?.type !== 'rag_call' || !messageData.payload) return;
      const payload = messageData.payload;
      const ragCallId = payload.rag_call_id || `rag-${Date.now()}`;

      setTranscripts((items) => {
        if (items.some((item) => item.id === ragCallId)) return items;
        return [
          ...items,
          {
            id: ragCallId,
            role: 'RagCall',
            text: JSON.stringify(payload),
            timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }),
          }
        ];
      });
    }, []),
  );

  const startConversation = async () => {
    if (!pcClient || isConnecting || !canStart) return;

    setError('');
    setIsConnecting(true);
    setLatencyStats([]);

    try {
      let convId = currentConversationId;
      if (!convId) {
        const res = await fetchWithAuth(`/api/conversations`, {
          method: 'POST',
          headers: { 
            'Content-Type': 'application/json'
          },
          body: JSON.stringify({ title: 'New conversation' })
        });
        if (res.ok) {
          const data = await res.json();
          convId = data.id;
          currentConversationIdRef.current = convId;
          setCurrentConversationId(convId);
          fetchConversations();
        }
      }

      await pcClient.startBotAndConnect({
        endpoint: START_ENDPOINT,
        requestData: {
          transport: 'webrtc',
          enableDefaultIceServers: true,
          body: {
            token: localStorage.getItem('aura_token'),
            conversation_id: convId,
          },
        },
      });
    } catch (err) {
      setError(err?.message || 'Could not connect to bot.py');
      setIsConnecting(false);
    }
  };

  const disconnect = async () => {
    setError('');
    setIsConnecting(false);
    try {
      await pcClient?.disconnect();
    } finally {
      activeBotMessageIdRef.current = null;
      botTextRef.current = '';
      onResetClient();
    }
  };

  return (
    <div className="app-container">
      <Sidebar
        isMemoryPanelOpen={isMemoryPanelOpen}
        toggleMemoryPanel={toggleMemoryPanel}
        memories={memories}
        deleteAllMemories={deleteAllMemories}
        deleteMemory={deleteMemory}
        isMemoryLoading={isMemoryLoading}
        memoryError={memoryError}
        sidebarTab={sidebarTab}
        setSidebarTab={setSidebarTab}
        startNewConversation={startNewConversation}
        conversations={conversations}
        currentConversationId={currentConversationId}
        loadConversation={loadConversation}
        deleteConversation={deleteConversation}
        fetchFiles={fetchFiles}
        ragFiles={ragFiles}
        isFilesLoading={isFilesLoading}
        isUploadingFile={isUploadingFile}
        uploadRagFile={uploadRagFile}
        isAddingLink={isAddingLink}
        linkUrl={linkUrl}
        setLinkUrl={setLinkUrl}
        addRagLink={addRagLink}
        fileError={fileError}
        deleteRagFile={deleteRagFile}
        inspectRagFile={setInspectedFile}
        latencyStats={latencyStats}
        clearLatencyStats={() => setLatencyStats([])}
      />

      <div className="main-stage">
        <div className="main-header">
          <div className="sidebar-title" style={{ margin: 0 }}>New conversation</div>
          <div className="status-indicator">
            <div className={`status-dot ${isActive ? 'active' : ''}`} style={isActive ? {backgroundColor: '#10b981'} : {}}></div>
            {statusLabel}
          </div>
        </div>
        
        <TranscriptPanel
          transcripts={transcripts}
          transcriptAreaRef={transcriptAreaRef}
          handleTranscriptScroll={handleTranscriptScroll}
          transcriptBottomRef={transcriptBottomRef}
          toggleToolCall={toggleToolCall}
          expandedToolCalls={expandedToolCalls}
        />

        <div className="voice-controls-area">
          <div className="voice-visualizer-container">
            {isActive ? (
              <VoiceVisualizer
                participantType="local"
                barColor="#7c3aed"
                barCount={24}
                barGap={4}
                barWidth={6}
                barMaxHeight={60}
              />
            ) : (
              <div className="wave-placeholder"></div>
            )}
          </div>
          
          <div className="mic-button-container">
            <button
              className={`mic-button ${isActive ? 'active' : ''}`}
              disabled={isConnecting || !canStart}
              onClick={startConversation}
            >
              <Mic className="mic-icon" strokeWidth={isActive ? 2 : 1.5} />
            </button>
            <div className="mic-label">{isActive ? 'Listening' : isConnecting ? 'Connecting...' : 'Click to start'}</div>
            {error ? <div className="error-message">{error}</div> : null}
            
            <div className="call-controls">
              <button
                className={`control-btn ${isMicEnabled ? '' : 'muted'}`}
                disabled={!isActive}
                onClick={() => enableMic(!isMicEnabled)}
                title={isMicEnabled ? 'Mute microphone' : 'Unmute microphone'}
              >
                <Mic size={18} strokeWidth={2} />
              </button>
              <button className="control-btn" disabled={!isActive} title="Bot audio is enabled">
                <Volume2 size={18} strokeWidth={2} />
              </button>
              <button
                className="control-btn"
                disabled={!isActive && !isConnecting}
                onClick={disconnect}
                style={{color: '#64748b'}}
                title="Disconnect"
              >
                <X size={18} strokeWidth={2} />
              </button>
            </div>
          </div>
        </div>
      </div>
      {inspectedFile ? (
        <ChunkInspector file={inspectedFile} onClose={() => setInspectedFile(null)} />
      ) : null}
    </div>
  );
}

function App() {
  const [isTokenValid, setIsTokenValid] = useState(() => isValidTokenValue(localStorage.getItem('aura_token')));
  const [client, setClient] = useState(() => createPipecatClient());

  React.useEffect(() => {
    const handleLogout = () => {
      localStorage.removeItem('aura_token');
      setIsTokenValid(false);
    };
    window.addEventListener('logout', handleLogout);
    return () => window.removeEventListener('logout', handleLogout);
  }, []);

  const handleLogin = useCallback((newToken) => {
    setIsTokenValid(isValidTokenValue(newToken));
  }, []);

  const resetClient = useCallback(() => {
    setClient(createPipecatClient());
  }, []);

  if (!isTokenValid) {
    return <Suspense fallback={null}><Auth onLogin={handleLogin} /></Suspense>;
  }

  return (
    <PipecatClientProvider client={client}>
      <Suspense fallback={<div className="app-container" />}><VoiceApp onResetClient={resetClient} /></Suspense>
      <PipecatClientAudio />
    </PipecatClientProvider>
  );
}

export default App;
