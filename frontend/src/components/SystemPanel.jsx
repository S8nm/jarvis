/**
 * System Dashboard — Shows weather, calendar, notes, scripts, and camera.
 * SpotlightCard hover effect on tiles, ShinyText on header.
 */
import SpotlightCard from './SpotlightCard';
import ShinyText from './ShinyText';

export default function SystemPanel({ dashboard }) {
    const {
        notes = { total: 0, pinned: 0, recent: [], tags: [] },
        calendar = { today_count: 0, today_events: [], upcoming_count: 0, upcoming_events: [] },
        camera_active = false,
        scripts = 0,
        weather = { available: false }
    } = dashboard || {};

    const formatTime = (isoStr) => {
        try {
            const d = new Date(isoStr);
            return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        } catch { return ''; }
    };

    return (
        <div className="system-panel glass-panel">
            <div className="panel-header">
                <span className="indicator" />
                <ShinyText speed={4}>System Dashboard</ShinyText>
            </div>

            <div className="dashboard-grid">
                {/* Weather Tile — spans full width */}
                {weather.available && (
                    <SpotlightCard className="dash-tile weather-tile">
                        <div className="tile-header">
                            <span className="tile-icon">{weather.icon || '🌤️'}</span>
                            <span className="tile-label">Weather</span>
                            <span className="tile-badge" style={{ fontSize: '0.7rem', color: 'var(--text-dim)' }}>
                                {weather.location}
                            </span>
                        </div>
                        <div className="tile-content weather-content">
                            <div className="weather-main">
                                <span className="weather-temp">{weather.temp_c}°C</span>
                                <span className="weather-condition">{weather.condition}</span>
                            </div>
                            <div className="weather-details">
                                <span>Feels {weather.feels_like_c}°C</span>
                                <span>H: {weather.high_c}° L: {weather.low_c}°</span>
                                <span>💧 {weather.humidity}%</span>
                                <span>💨 {weather.wind_kph} km/h</span>
                            </div>
                        </div>
                    </SpotlightCard>
                )}

                {/* Calendar Tile */}
                <SpotlightCard className="dash-tile">
                    <div className="tile-header">
                        <span className="tile-icon">📅</span>
                        <span className="tile-label">Calendar</span>
                        {calendar.today_count > 0 && (
                            <span className="tile-badge-count">{calendar.today_count}</span>
                        )}
                    </div>
                    <div className="tile-content">
                        {calendar.today_count > 0 ? (
                            <div className="tile-list">
                                {calendar.today_events.slice(0, 3).map((evt, i) => (
                                    <div key={i} className="tile-list-item">
                                        <span className="tile-time">{formatTime(evt.start_time)}</span>
                                        <span className="tile-text">{evt.title}</span>
                                    </div>
                                ))}
                            </div>
                        ) : (
                            <div className="tile-empty">No events today</div>
                        )}
                        {calendar.upcoming_count > 0 && (
                            <div className="tile-footer">
                                {calendar.upcoming_count} upcoming this week
                            </div>
                        )}
                    </div>
                </SpotlightCard>

                {/* Notes Tile */}
                <SpotlightCard className="dash-tile">
                    <div className="tile-header">
                        <span className="tile-icon">📝</span>
                        <span className="tile-label">Notes</span>
                        {notes.pinned > 0 && <span className="tile-badge">📌 {notes.pinned}</span>}
                    </div>
                    <div className="tile-content">
                        {notes.total > 0 ? (
                            <div className="tile-list">
                                {notes.recent?.slice(0, 3).map((note, i) => (
                                    <div key={i} className="tile-list-item">
                                        <span className="tile-tag">{note.tag}</span>
                                        <span className="tile-text">
                                            {note.content.substring(0, 50)}{note.content.length > 50 ? '...' : ''}
                                        </span>
                                    </div>
                                ))}
                            </div>
                        ) : (
                            <div className="tile-empty">Say "note that..." to save notes</div>
                        )}
                    </div>
                </SpotlightCard>

                {/* Scripts Tile */}
                <SpotlightCard className="dash-tile">
                    <div className="tile-header">
                        <span className="tile-icon">⚡</span>
                        <span className="tile-label">Scripts</span>
                    </div>
                    <div className="tile-content">
                        <div className="tile-stat">{scripts} {scripts === 1 ? 'script' : 'scripts'}</div>
                        <div className="tile-empty">
                            {scripts === 0 ? 'Ask to generate code' : 'Ready in sandbox/scripts'}
                        </div>
                    </div>
                </SpotlightCard>

                {/* Camera Tile */}
                <SpotlightCard className="dash-tile">
                    <div className="tile-header">
                        <span className="tile-icon">🎥</span>
                        <span className="tile-label">Camera</span>
                    </div>
                    <div className="tile-content">
                        <div className="tile-stat">
                            <span className={`status-dot ${camera_active ? 'active' : ''}`}></span>
                            {camera_active ? 'Active' : 'Standby'}
                        </div>
                        <div className="tile-empty">Say "look at this" to activate</div>
                    </div>
                </SpotlightCard>
            </div>
        </div>
    );
}
