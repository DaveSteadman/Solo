import {
    createControlRow,
    createHeading,
    createPage,
    createParagraph
} from "./basic-layout.js";
import { createChatExchange } from "./chat-exchange.js";
import { createCheckbox } from "./checkbox.js";
import { createCodeEditor } from "./code-editor.js";
import { createFileExplorer } from "./file-explorer.js";
import { createIconButton } from "./icon-button.js";
import { createIconPanel } from "./icon-panel.js";
import { createIconTextButton } from "./icon-text-button.js";
import { createLineEdit } from "./line-edit.js";
import { createSliderPanel } from "./slider-panel.js";
import { createTextButton } from "./text-button.js";
import { createTextArea } from "./text-area.js";
import { createTextLabel } from "./text-label.js";

const controlFactories = {
    chatExchange: createChatExchange,
    checkbox: createCheckbox,
    codeEditor: createCodeEditor,
    controlRow: (spec) => createControlRow(spec, createControl),
    fileExplorer: createFileExplorer,
    heading: createHeading,
    iconButton: createIconButton,
    iconPanel: (spec) => createIconPanel(spec, createControl),
    iconTextButton: createIconTextButton,
    lineEdit: createLineEdit,
    paragraph: createParagraph,
    sliderPanel: (spec) => createSliderPanel(spec, createControl),
    textButton: createTextButton,
    textArea: createTextArea,
    textLabel: createTextLabel
};

export function renderPage(spec, mount) {
    mount.replaceChildren(createPage(spec, createControl));
}

function createControl(spec) {
    const factory = controlFactories[spec.type];

    if (!factory) {
        throw new Error(`Unknown component type: ${spec.type}`);
    }

    return factory(spec);
}
