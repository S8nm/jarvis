import { useState, useEffect, useRef, memo } from 'react';
import DecryptedText from './DecryptedText';

function getGreeting() {
    const h = new Date().getHours();
    if (h < 5) return 'Good evening';
    if (h < 12) return 'Good morning';
    if (h < 17) return 'Good afternoon';
    return 'Good evening';
}

/**
 * GreetingClock — Prominent time display with decrypt-animated greeting.
 */
const GreetingClock = memo(function GreetingClock() {
    const [now, setNow] = useState(new Date());
    const prevGreeting = useRef('');
    const [greetingKey, setGreetingKey] = useState(0);

    useEffect(() => {
        const id = setInterval(() => setNow(new Date()), 1000);
        return () => clearInterval(id);
    }, []);

    const greeting = getGreeting();

    // Re-trigger decrypt animation when greeting changes (morning→afternoon etc.)
    useEffect(() => {
        if (prevGreeting.current !== greeting) {
            prevGreeting.current = greeting;
            setGreetingKey(k => k + 1);
        }
    }, [greeting]);

    const timeStr = now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    const secStr = now.toLocaleTimeString([], { second: '2-digit' }).split(' ')[0];
    const dateStr = now.toLocaleDateString([], {
        weekday: 'long',
        month: 'long',
        day: 'numeric',
        year: 'numeric'
    });

    return (
        <div className="greeting-clock">
            <div className="greeting-text">
                <DecryptedText
                    key={greetingKey}
                    text={`${greeting}, sir.`}
                    speed={40}
                    revealDelay={300}
                    sequential
                />
            </div>
            <div className="clock-display">
                <span className="clock-time">{timeStr}</span>
                <span className="clock-seconds">{secStr.slice(-2)}</span>
            </div>
            <div className="clock-date">{dateStr}</div>
        </div>
    );
});

export default GreetingClock;
