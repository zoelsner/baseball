import React from 'react';
import { createRoot } from 'react-dom/client';
import { V2App } from './v2-pages.jsx';

const root = createRoot(document.getElementById('root'));
root.render(<V2App initial={{ page:'today' }}/>);
