import {useCallback, useState} from 'react';
import {checkStorm, directionTo, newStorm} from './config.js';

export function useStormScenario(initial) {
  const [config, setConfig] = useState(initial || newStorm);
  const [placement, setPlacement] = useState(null);
  const [message, setMessage] = useState('Place the start in the ocean, then choose a direction.');
  const changePlacement = useCallback(next => {
    setPlacement(next);
    setMessage(next === 'origin' ? 'Tap the ocean to place the storm’s starting point.'
      : next === 'direction' ? 'Tap the map toward the destination.' : 'Storm ready. Adjust the settings or simulate.');
  }, []);
  const place = useCallback((point, isLand = false) => {
    if (placement === 'origin') {
      if (isLand) {setMessage('Choose a starting point in the ocean.');return;}
      const next = {...config, origin: {lon: Math.round(point[0] * 10000) / 10000, lat: Math.round(point[1] * 10000) / 10000}};
      const problem = checkStorm(next);
      if (problem) {setMessage(problem);return;}
      setConfig(next);changePlacement('direction');
    } else if (placement === 'direction') {
      setConfig(c => directionTo(c, point));setPlacement(null);
      setMessage('Storm ready. Drag the arrow to adjust direction or simulate.');
    }
  }, [placement, config, changePlacement]);
  return {config, setConfig, placement, setPlacement: changePlacement, place, message};
}
