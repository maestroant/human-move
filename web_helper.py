from playwright.async_api import Page

OVERLAY_ROOT_ID = "__human_move_overlay__"

async def ensure_overlay(page: Page) -> None:
    await page.evaluate(
        """overlayId => {
            if (document.getElementById(overlayId)) {
                return;
            }

            const root = document.createElement("div");
            root.id = overlayId;
            root.innerHTML = `
              <style>
                #${overlayId} {
                  position: fixed;
                  inset: 0;
                  pointer-events: none;
                  z-index: 2147483647;
                }
                #${overlayId} .hm-trail {
                  position: absolute;
                  inset: 0;
                }
                #${overlayId} .hm-dot {
                  position: absolute;
                  width: 6px;
                  height: 6px;
                  margin-left: -3px;
                  margin-top: -3px;
                  border-radius: 999px;
                  background: rgba(11, 87, 208, 0.35);
                }
                #${overlayId} .hm-cursor {
                  position: absolute;
                  width: 18px;
                  height: 18px;
                  margin-left: -9px;
                  margin-top: -9px;
                  border: 2px solid #0b57d0;
                  border-radius: 999px;
                  background: rgba(11, 87, 208, 0.12);
                  box-shadow: 0 0 0 6px rgba(11, 87, 208, 0.08);
                }
              </style>
              <div class="hm-trail"></div>
              <div class="hm-cursor"></div>
            `;

            document.documentElement.append(root);
        }""",
        OVERLAY_ROOT_ID,
    )

async def clear_overlay(page: Page) -> None:
    await page.evaluate(
        """overlayId => {
            const root = document.getElementById(overlayId);
            if (!root) {
                return;
            }

            const trail = root.querySelector(".hm-trail");
            const cursor = root.querySelector(".hm-cursor");
            if (trail) {
                trail.replaceChildren();
            }
            if (cursor) {
                cursor.style.left = "0px";
                cursor.style.top = "0px";
                cursor.style.opacity = "0";
            }
        }""",
        OVERLAY_ROOT_ID,
    )

async def update_overlay(page: Page, x: int, y: int) -> None:
    await page.evaluate(
        """({ overlayId, x, y }) => {
            const root = document.getElementById(overlayId);
            if (!root) {
                return;
            }

            const trail = root.querySelector(".hm-trail");
            const cursor = root.querySelector(".hm-cursor");
            if (trail) {
                const dot = document.createElement("div");
                dot.className = "hm-dot";
                dot.style.left = `${x}px`;
                dot.style.top = `${y}px`;
                trail.append(dot);
            }
            if (cursor) {
                cursor.style.left = `${x}px`;
                cursor.style.top = `${y}px`;
                cursor.style.opacity = "1";
            }
        }""",
        {"overlayId": OVERLAY_ROOT_ID, "x": x, "y": y},
    )

async def set_page_status(page: Page, text: str) -> None:
    await page.evaluate(
        """text => {
            const node = document.getElementById("status");
            if (node) {
                node.textContent = `Status: ${text}`;
            }
        }""",
        text,
    )