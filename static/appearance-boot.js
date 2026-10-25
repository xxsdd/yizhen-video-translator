'use strict';
// Apply the saved palette before styles render, without an API request.
try{document.documentElement.dataset.theme=localStorage.getItem('yizhen-theme')==='light'?'light':'dark'}
catch{document.documentElement.dataset.theme='dark'}
