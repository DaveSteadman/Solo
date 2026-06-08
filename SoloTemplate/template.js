import { renderPage } from "../SoloCommonWebUI/framework/js/renderer.js";

const mount = document.querySelector("#app");
const specUrl = document.body.dataset.pageSpec ?? "./page.json";

try {
    const response = await fetch(specUrl);

    if (!response.ok) {
        throw new Error(`Could not load ${specUrl}`);
    }

    renderPage(await response.json(), mount);
} catch (error) {
    mount.textContent = error.message;
}
