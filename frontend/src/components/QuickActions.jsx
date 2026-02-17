// QuickActions — shortcut buttons for common commands

const QUICK_COMMANDS = [
    { label: '🕐 Time', command: 'What time is it?' },
    { label: '📅 Calendar', command: 'Check my calendar for today' },
    { label: '📝 Notes', command: 'Show my recent notes' },
    { label: '💻 System', command: 'How is my system doing?' },
    { label: '🔍 Search', command: 'Search the web for ' },
    { label: '⚡ Script', command: 'Generate a Python script that ' },
];

/**
 * QuickActions — Row of shortcut buttons for common commands.
 * Clicking a button sends it as text input to the backend.
 */
export default function QuickActions({ sendText, disabled }) {
    const handleClick = (cmd) => {
        if (disabled) return;
        // If command ends with a space, it's a prompt — put in input instead
        if (cmd.endsWith(' ')) return; // Could be extended to prefill input
        sendText(cmd);
    };

    return (
        <div className="quick-actions">
            <div className="quick-actions-label">QUICK COMMANDS</div>
            <div className="quick-actions-grid">
                {QUICK_COMMANDS.map((item, i) => (
                    <button
                        key={i}
                        className="quick-action-btn"
                        onClick={() => handleClick(item.command)}
                        disabled={disabled}
                        title={item.command}
                    >
                        {item.label}
                    </button>
                ))}
            </div>
        </div>
    );
}
