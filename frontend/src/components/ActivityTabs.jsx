import { useState, memo } from 'react';
import { useConnection, useSystem } from '../contexts/JarvisContext';
import './ActivityTabs.css';

/**
 * Activity Tabs - Shows active tools, LLM usage, and system activities.
 * Now consumes context directly — no props needed.
 */
const ActivityTabs = memo(function ActivityTabs() {
    const { agentState } = useConnection();
    const { toolActivity, status } = useSystem();

    const [minimized, setMinimized] = useState(false);

    const currentLLM = status?.current_llm;

    const tabs = [];

    tabs.push({
        id: 'llm',
        title: 'LLM MODEL',
        type: 'llm',
        content: currentLLM || 'Connecting...',
        status: 'active'
    });

    if (agentState !== 'IDLE') {
        tabs.push({
            id: 'agent-state',
            title: 'AGENT STATUS',
            type: 'status',
            content: agentState,
            status: agentState === 'THINKING' ? 'processing' : agentState === 'EXECUTING' ? 'executing' : 'active'
        });
    }

    if (toolActivity) {
        tabs.push({
            id: 'tool',
            title: `TOOL: ${toolActivity.tool.toUpperCase()}`,
            type: 'tool',
            content: toolActivity,
            status: toolActivity.status === 'executing' ? 'executing' : 'completed'
        });
    }

    if (status?.dashboard?.camera_active) {
        tabs.push({
            id: 'camera',
            title: 'CAMERA ACTIVE',
            type: 'camera',
            content: 'Camera module active',
            status: 'active'
        });
    }

    if (status?.system) {
        const sys = status.system;
        tabs.push({
            id: 'resources',
            title: 'SYSTEM RESOURCES',
            type: 'resources',
            content: {
                cpu: sys.cpu_percent,
                memory: sys.memory_percent,
                disk_read: sys.disk_read_mb,
                disk_write: sys.disk_write_mb
            },
            status: 'active'
        });
    }

    if (tabs.length === 0) return null;

    return (
        <div className={`activity-tabs ${minimized ? 'minimized' : ''}`} aria-label="Active processes">
            <div className="tabs-header">
                <span className="tabs-title">◈ ACTIVE PROCESSES</span>
                <button
                    className="tabs-minimize-btn"
                    onClick={() => setMinimized(!minimized)}
                    title={minimized ? 'Expand' : 'Minimize'}
                    aria-expanded={!minimized}
                    aria-label={minimized ? 'Expand active processes' : 'Minimize active processes'}
                >
                    {minimized ? '▲' : '▼'}
                </button>
            </div>

            {!minimized && (
                <div className="tabs-container">
                    {tabs.map(tab => (
                        <TabCard key={tab.id} tab={tab} />
                    ))}
                </div>
            )}
        </div>
    );
});

export default ActivityTabs;

function truncateJson(obj, maxLen = 300) {
    try {
        const str = JSON.stringify(obj, null, 2);
        if (str.length <= maxLen) return str;
        return str.slice(0, maxLen) + '\n... [truncated]';
    } catch {
        return String(obj).slice(0, maxLen);
    }
}

function TabCard({ tab }) {
    const renderContent = () => {
        switch (tab.type) {
            case 'llm':
                return (
                    <div className="tab-content">
                        <div className="tab-label">Model:</div>
                        <div className="tab-value">{tab.content}</div>
                    </div>
                );

            case 'status':
                return (
                    <div className="tab-content">
                        <div className="tab-label">State:</div>
                        <div className="tab-value">{tab.content}</div>
                    </div>
                );

            case 'tool': {
                const tool = tab.content;
                return (
                    <div className="tab-content">
                        <div className="tab-label">Tool:</div>
                        <div className="tab-value">{tool.tool}</div>
                        {tool.args && (
                            <div className="tab-args">
                                <div className="tab-label">Args:</div>
                                <div className="tab-value-small">{truncateJson(tool.args)}</div>
                            </div>
                        )}
                        {tool.result && (
                            <div className="tab-result">
                                <div className="tab-label">Result:</div>
                                <div className="tab-value-small">{truncateJson(tool.result)}</div>
                            </div>
                        )}
                    </div>
                );
            }

            case 'camera':
                return (
                    <div className="tab-content">
                        <div className="tab-label">Status:</div>
                        <div className="tab-value">{tab.content}</div>
                    </div>
                );

            case 'resources': {
                const res = tab.content;
                return (
                    <div className="tab-content tab-resources">
                        <div className="resource-row">
                            <span className="resource-label">CPU:</span>
                            <span className="resource-value">{res.cpu}%</span>
                        </div>
                        <div className="resource-row">
                            <span className="resource-label">Memory:</span>
                            <span className="resource-value">{res.memory}%</span>
                        </div>
                        <div className="resource-row">
                            <span className="resource-label">Disk R:</span>
                            <span className="resource-value">{res.disk_read} MB</span>
                        </div>
                        <div className="resource-row">
                            <span className="resource-label">Disk W:</span>
                            <span className="resource-value">{res.disk_write} MB</span>
                        </div>
                    </div>
                );
            }

            default:
                return <div className="tab-content">{tab.content}</div>;
        }
    };

    return (
        <div className={`tab-card ${tab.status}`}>
            <div className="tab-card-header">
                <span className={`tab-indicator ${tab.status}`} aria-hidden="true" />
                <span className="tab-card-title">{tab.title}</span>
            </div>
            {renderContent()}
        </div>
    );
}
