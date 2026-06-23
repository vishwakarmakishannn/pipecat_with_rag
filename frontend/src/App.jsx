import React, { useCallback, useMemo, useState } from 'react';
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
import { Plus, Mic, Volume2, X, User, MessageSquare, LogOut, Trash2, Wrench, ChevronDown, ChevronRight, Brain, FileText, Upload, RefreshCw } from 'lucide-react';
import { jwtDecode } from 'jwt-decode';
import Auth from './components/Auth';
import './App.css';

const START_ENDPOINT =
  import.meta.env.VITE_PIPECAT_START_URL ||
  `${window.location.protocol}//${window.location.hostname}:7860/start`;

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
  const [fileError, setFileError] = useState('');
  const currentConversationIdRef = React.useRef(null);
  const transcriptAreaRef = React.useRef(null);
  const transcriptBottomRef = React.useRef(null);
  const shouldAutoScrollRef = React.useRef(true);
  const botTextRef = React.useRef('');
  const activeBotMessageIdRef = React.useRef(null);
  const toolCallPayloadsRef = React.useRef({});
  const savedToolCallIdsRef = React.useRef(new Set());
  const [expandedToolCalls, setExpandedToolCalls] = useState({});

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
      const res = await fetch('http://localhost:7860/api/conversations', {
        headers: { Authorization: `Bearer ${localStorage.getItem('aura_token')}` }
      });
      if (res.ok) setConversations(await res.json());
    } catch (err) {
      console.warn('Failed to fetch conversations', err);
    }
  }, []);

  const fetchMemories = useCallback(async () => {
    setIsMemoryLoading(true);
    setMemoryError('');
    try {
      const res = await fetch('http://localhost:7860/api/memories', {
        headers: { Authorization: `Bearer ${localStorage.getItem('aura_token')}` }
      });
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
      const res = await fetch('http://localhost:7860/api/files', {
        headers: { Authorization: `Bearer ${localStorage.getItem('aura_token')}` }
      });
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
      const res = await fetch(`http://localhost:7860/api/conversations/${id}/messages`, {
        headers: { Authorization: `Bearer ${localStorage.getItem('aura_token')}` }
      });
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
      const res = await fetch(`http://localhost:7860/api/conversations/${id}`, {
        method: 'DELETE',
        headers: { Authorization: `Bearer ${localStorage.getItem('aura_token')}` }
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
      const res = await fetch(`http://localhost:7860/api/memories/${id}`, {
        method: 'DELETE',
        headers: { Authorization: `Bearer ${localStorage.getItem('aura_token')}` }
      });
      if (!res.ok) throw new Error('Could not delete memory');
      setMemories((items) => items.filter((memory) => memory.id !== id));
    } catch (err) {
      setMemoryError(err?.message || 'Could not delete memory');
    }
  };

  const deleteAllMemories = async () => {
    try {
      const res = await fetch('http://localhost:7860/api/memories', {
        method: 'DELETE',
        headers: { Authorization: `Bearer ${localStorage.getItem('aura_token')}` }
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
      const res = await fetch('http://localhost:7860/api/files', {
        method: 'POST',
        headers: { Authorization: `Bearer ${localStorage.getItem('aura_token')}` },
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

  const deleteRagFile = async (id) => {
    try {
      const res = await fetch(`http://localhost:7860/api/files/${id}`, {
        method: 'DELETE',
        headers: { Authorization: `Bearer ${localStorage.getItem('aura_token')}` }
      });
      if (!res.ok) throw new Error('Could not delete file');
      setRagFiles((items) => items.filter((file) => file.id !== id));
    } catch (err) {
      setFileError(err?.message || 'Could not delete file');
    }
  };

  const formatFileSize = (sizeBytes) => {
    if (!sizeBytes) return '0 KB';
    if (sizeBytes < 1024 * 1024) return `${Math.max(1, Math.round(sizeBytes / 1024))} KB`;
    return `${(sizeBytes / (1024 * 1024)).toFixed(1)} MB`;
  };

  const startNewConversation = async () => {
    if (isActive) await disconnect();
    currentConversationIdRef.current = null;
    setCurrentConversationId(null);
    setTranscripts([]);
  };

  const saveToolCallTranscript = useCallback(async (payload) => {
    const convId = currentConversationIdRef.current;
    if (!convId) return;

    try {
      const res = await fetch(`http://localhost:7860/api/conversations/${convId}/messages`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${localStorage.getItem('aura_token')}`
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

  const startConversation = async () => {
    if (!pcClient || isConnecting || !canStart) return;

    setError('');
    setIsConnecting(true);

    try {
      let convId = currentConversationId;
      if (!convId) {
        const res = await fetch('http://localhost:7860/api/conversations', {
          method: 'POST',
          headers: { 
            'Content-Type': 'application/json',
            Authorization: `Bearer ${localStorage.getItem('aura_token')}`
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
      <div className="sidebar">
        <div className="sidebar-header">
          <div className="brand">
            <div className="brand-icon">A</div>
            Aura Voice
          </div>
          <div className="sidebar-controls">
            <button className={`icon-btn ${isMemoryPanelOpen ? 'active' : ''}`} onClick={toggleMemoryPanel} title="Saved Memories">
              <Brain size={16} strokeWidth={2.5} />
            </button>
            <button className="icon-btn" onClick={startNewConversation} title="New Conversation">
              <Plus size={16} strokeWidth={2.5} />
            </button>
            <button className="icon-btn logout-btn" onClick={() => window.dispatchEvent(new Event('logout'))} title="Logout">
              <LogOut size={16} strokeWidth={2.5} />
            </button>
          </div>
        </div>
        {isMemoryPanelOpen ? (
          <div className="memory-panel">
            <div className="memory-popover-header">
              <div>
                <div className="memory-popover-title">Saved memories</div>
                <div className="memory-popover-subtitle">{memories.length} active</div>
              </div>
              <button
                className="memory-delete-all"
                onClick={deleteAllMemories}
                disabled={!memories.length || isMemoryLoading}
              >
                Delete all
              </button>
            </div>

            {memoryError ? <div className="memory-error">{memoryError}</div> : null}

            <div className="memory-list">
              {isMemoryLoading ? (
                <div className="memory-empty">Loading...</div>
              ) : memories.length ? (
                memories.map((memory) => (
                  <div className="memory-item" key={memory.id}>
                    <div className="memory-item-text">
                      <div className="memory-label">
                        {memory.key.replaceAll('_', ' ')}
                        <span>{memory.fact_type}</span>
                      </div>
                      <div className="memory-value">{memory.value}</div>
                    </div>
                    <button
                      className="memory-delete-btn"
                      onClick={() => deleteMemory(memory.id)}
                      title="Delete memory"
                    >
                      <Trash2 size={14} />
                    </button>
                  </div>
                ))
              ) : (
                <div className="memory-empty">No saved memories yet</div>
              )}
            </div>
          </div>
        ) : null}
        <div className="sidebar-tabs">
          <button
            className={`sidebar-tab ${sidebarTab === 'chats' ? 'active' : ''}`}
            onClick={() => setSidebarTab('chats')}
          >
            Chats
          </button>
          <button
            className={`sidebar-tab ${sidebarTab === 'files' ? 'active' : ''}`}
            onClick={() => {
              setSidebarTab('files');
              fetchFiles();
            }}
          >
            Files
          </button>
        </div>

        <div className="sidebar-content" style={{ overflowY: 'auto' }}>
          {sidebarTab === 'chats' ? (
            conversations.length === 0 ? (
              <>
                <div className="sidebar-title">No conversations</div>
                <div className="sidebar-subtitle">Your history will appear here</div>
              </>
            ) : (
              <div className="history-list">
                {conversations.map(conv => (
                  <div 
                    key={conv.id} 
                    className={`history-item ${currentConversationId === conv.id ? 'active' : ''}`}
                    onClick={() => loadConversation(conv.id)}
                  >
                    <MessageSquare size={16} className="history-icon" />
                    <div className="history-text">
                      <div className="history-title">{conv.title}</div>
                      <div className="history-time">{new Date(conv.created_at).toLocaleDateString()}</div>
                    </div>
                    <button className="delete-btn" onClick={(e) => deleteConversation(e, conv.id)} title="Delete">
                      <Trash2 size={14} />
                    </button>
                  </div>
                ))}
              </div>
            )
          ) : (
            <div className="files-panel">
              <label className={`file-upload ${isUploadingFile ? 'disabled' : ''}`}>
                <Upload size={16} />
                <span>{isUploadingFile ? 'Uploading...' : 'Upload PDF'}</span>
                <input
                  type="file"
                  accept="application/pdf,.pdf"
                  disabled={isUploadingFile}
                  onChange={uploadRagFile}
                />
              </label>
              <button className="file-refresh-btn" onClick={fetchFiles} disabled={isFilesLoading} title="Refresh files">
                <RefreshCw size={15} className={isFilesLoading ? 'spinning' : ''} />
              </button>

              {fileError ? <div className="file-error">{fileError}</div> : null}

              <div className="file-list">
                {isFilesLoading && !ragFiles.length ? (
                  <div className="file-empty">Loading files...</div>
                ) : ragFiles.length ? (
                  ragFiles.map((file) => (
                    <div className="file-item" key={file.id}>
                      <FileText size={17} className="file-icon" />
                      <div className="file-details">
                        <div className="file-name" title={file.filename}>{file.filename}</div>
                        <div className="file-meta">
                          {formatFileSize(file.size_bytes)} · {file.chunk_count} chunks
                        </div>
                        {file.error ? <div className="file-error-text">{file.error}</div> : null}
                      </div>
                      <div className="file-actions">
                        <span className={`file-status ${file.status}`}>{file.status}</span>
                        <button className="delete-btn file-delete" onClick={() => deleteRagFile(file.id)} title="Delete file">
                          <Trash2 size={14} />
                        </button>
                      </div>
                    </div>
                  ))
                ) : (
                  <div className="file-empty">No PDFs uploaded yet</div>
                )}
              </div>
            </div>
          )}
        </div>
      </div>

      <div className="main-stage">
        <div className="main-header">
          <div className="sidebar-title" style={{ margin: 0 }}>New conversation</div>
          <div className="status-indicator">
            <div className={`status-dot ${isActive ? 'active' : ''}`} style={isActive ? {backgroundColor: '#10b981'} : {}}></div>
            {statusLabel}
          </div>
        </div>
        
        <div
          className="transcription-area"
          ref={transcriptAreaRef}
          onScroll={handleTranscriptScroll}
        >
          {transcripts.length ? (
            <div className="transcript-list">
              {transcripts.map((item) => (
                <div className="transcript-item" key={item.id}>
                  <div className={`transcript-avatar ${item.role === 'You' ? 'user-avatar' : item.role === 'ToolCall' ? 'tool-avatar' : 'bot-avatar'}`}>
                    {item.role === 'You' ? <User size={18} strokeWidth={2.5} /> : item.role === 'ToolCall' ? <Wrench size={16} /> : 'A'}
                  </div>
                  <div className="transcript-message">
                    <div className="transcript-role">
                      {item.role === 'Aura' ? 'Aura AI' : item.role === 'ToolCall' ? 'Tool Call' : item.role}
                      {item.timestamp && <span className="transcript-time">{item.timestamp}</span>}
                    </div>
                    {item.role === 'ToolCall' ? (() => {
                      let parsed;
                      try { parsed = JSON.parse(item.text); } catch { parsed = { function_name: 'Unknown', arguments: item.text }; }
                      return (
                        <div className="tool-call-block">
                          <div className="tool-call-header" onClick={() => toggleToolCall(item.id)}>
                            {expandedToolCalls[item.id] ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                            <span style={{fontWeight: 500, color: '#475569'}}>{parsed.function_name}</span>
                          </div>
                          {expandedToolCalls[item.id] && (
                            <div className="tool-call-body">
                              <div className="tool-call-section">
                                <strong style={{fontSize: '0.75rem', color: '#64748b'}}>Arguments</strong>
                                <pre>{JSON.stringify(parsed.arguments, null, 2)}</pre>
                              </div>
                              {parsed.result && (
                                <div className="tool-call-section" style={{marginTop: '8px', borderTop: '1px solid #e2e8f0', paddingTop: '8px'}}>
                                  <strong style={{fontSize: '0.75rem', color: '#64748b'}}>Result</strong>
                                  <pre style={{maxHeight: '200px', overflowY: 'auto'}}>{JSON.stringify(parsed.result, null, 2)}</pre>
                                </div>
                              )}
                            </div>
                          )}
                        </div>
                      );
                    })() : (
                      <div className="transcript-text">{item.text}</div>
                    )}
                  </div>
                </div>
              ))}
              <div ref={transcriptBottomRef} aria-hidden="true" />
            </div>
          ) : (
            <div className="empty-state">
               <Mic size={48} color="#cbd5e1" strokeWidth={1} />
               <div className="empty-text">Start talking to Aura Voice</div>
            </div>
          )}
        </div>

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
    return <Auth onLogin={handleLogin} />;
  }

  return (
    <PipecatClientProvider client={client}>
      <VoiceApp onResetClient={resetClient} />
      <PipecatClientAudio />
    </PipecatClientProvider>
  );
}

export default App;
