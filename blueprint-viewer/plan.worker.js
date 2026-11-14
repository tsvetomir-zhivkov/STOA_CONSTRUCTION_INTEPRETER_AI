import {generatePlan} from './plans.js';
self.onmessage=({data})=>{try{self.postMessage(generatePlan(data));}catch(error){self.postMessage({error:error.message});}};
