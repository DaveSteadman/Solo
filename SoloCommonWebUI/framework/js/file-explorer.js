import { el } from "./dom.js";

export function createFileExplorer(spec) {
    const tree = readTree(spec);
    const state = {
        selectedPath: spec.selectedPath ?? null,
        openFolders: new Set(spec.openFolders ?? [])
    };
    const list = el("div", {
        className: "file-explorer-tree",
        attrs: {
            "aria-label": spec.label ?? "File explorer",
            "role": "tree"
        }
    });
    const explorer = el("section", {
        className: "file-explorer",
        attrs: {
            "aria-label": spec.label ?? "File explorer"
        }
    }, [list]);

    render();
    return explorer;

    function render() {
        list.replaceChildren(...tree.map((item) => createItem(item, 0)));
    }

    function createItem(item, depth) {
        const type = item.type === "file" ? "file" : "folder";
        const path = item.path ?? item.name ?? "";
        const isOpen = type === "folder" && state.openFolders.has(path);
        const isSelected = type === "file" && state.selectedPath === path;
        const isActiveFolder = type === "folder" && (isOpen || isAncestorOfSelected(path));
        const rowClass = [
            "file-explorer-row",
            "font-normal",
            `file-explorer-row--${type}`,
            isOpen ? "is-open" : "",
            isSelected ? "is-selected" : "",
            isActiveFolder ? "is-active-folder" : ""
        ].filter(Boolean).join(" ");
        const row = el("button", {
            className: rowClass,
            style: {
                "--file-explorer-depth": depth
            },
            attrs: {
                "aria-expanded": type === "folder" ? String(isOpen) : undefined,
                "aria-selected": type === "file" ? String(isSelected) : undefined,
                "data-path": path,
                "role": "treeitem",
                "type": "button"
            }
        }, [
            el("span", { className: "file-explorer-twist", text: type === "folder" ? (isOpen ? "v" : ">") : "" }),
            el("span", { className: "file-explorer-name", text: item.name ?? path })
        ]);

        row.addEventListener("click", () => {
            if (type === "folder") {
                toggleFolder(path);
                return;
            }
            selectFile(item);
        });

        const children = type === "folder" && isOpen
            ? el("div", { className: "file-explorer-children", attrs: { role: "group" } }, (item.children ?? []).map((child) => createItem(child, depth + 1)))
            : null;

        return el("div", { className: "file-explorer-item" }, [row, children]);
    }

    function toggleFolder(path) {
        if (state.openFolders.has(path)) {
            state.openFolders.delete(path);
        } else {
            state.openFolders.add(path);
        }
        render();
        explorer.dispatchEvent(new CustomEvent("solo:folderToggle", {
            bubbles: true,
            detail: {
                path,
                openFolders: [...state.openFolders]
            }
        }));
    }

    function selectFile(item) {
        state.selectedPath = item.path ?? item.name ?? "";
        render();
        explorer.dispatchEvent(new CustomEvent("solo:fileSelect", {
            bubbles: true,
            detail: {
                action: spec.fileSelectAction ?? "openFile",
                file: item
            }
        }));
    }

    function isAncestorOfSelected(path) {
        return Boolean(state.selectedPath && state.selectedPath.startsWith(`${path}/`));
    }
}

function readTree(spec) {
    if (Array.isArray(spec.items)) {
        return spec.items;
    }
    if (!spec.itemsJson) {
        return [];
    }
    try {
        const parsed = JSON.parse(spec.itemsJson);
        return Array.isArray(parsed) ? parsed : [];
    } catch {
        return [
            {
                name: "Invalid file tree JSON",
                path: "invalid-file-tree-json",
                type: "file"
            }
        ];
    }
}
