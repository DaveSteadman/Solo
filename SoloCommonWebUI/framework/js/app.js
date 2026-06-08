import { renderPage } from "./renderer.js";

const specUrl = "../../SoloTemplate/page.json";
const mount = document.querySelector("#app");

try {
    const response = await fetch(specUrl);

    if (!response.ok) {
        throw new Error(`Could not load ${specUrl}`);
    }

    renderPage(await response.json(), mount);
} catch (error) {
    mount.textContent = error.message;
}
