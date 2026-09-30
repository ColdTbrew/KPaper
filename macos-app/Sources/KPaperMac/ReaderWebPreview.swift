import SwiftUI
import WebKit

struct ReaderHeading: Identifiable {
    let id: String
    let title: String
    let level: Int
}

enum ReaderProgressStore {
    static func key(_ url: URL) -> String { "reader.progress." + url.standardizedFileURL.path }
    static func mode(for url: URL) -> Int { (UserDefaults.standard.dictionary(forKey: key(url))?["mode"] as? Int) ?? 0 }
}

struct ReaderOutline: View {
    let headings: [ReaderHeading]
    let currentAnchor: String?
    let select: (String) -> Void
    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Text("목차").font(.system(size: 13, weight: .semibold)).padding(14)
            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 2) {
                        ForEach(headings) { heading in
                            Button { select(heading.id) } label: {
                                Text(heading.title)
                                    .font(.system(size: 12, weight: currentAnchor == heading.id ? .semibold : .regular))
                                    .foregroundStyle(currentAnchor == heading.id ? Color.accentColor : .secondary)
                                    .lineLimit(3)
                                    .frame(maxWidth: .infinity, alignment: .leading)
                                    .padding(.leading, CGFloat(max(0, heading.level - 2)) * 10)
                                    .padding(8)
                                    .background(currentAnchor == heading.id ? Color.accentColor.opacity(0.10) : .clear)
                                    .clipShape(RoundedRectangle(cornerRadius: 6))
                            }.buttonStyle(.plain).id(heading.id)
                        }
                    }.padding(.horizontal, 6)
                }
                .onChange(of: currentAnchor) { anchor in
                    if let anchor { proxy.scrollTo(anchor, anchor: .center) }
                }
            }
        }.frame(width: 210).background(Color(nsColor: .controlBackgroundColor).opacity(0.72))
    }
}

struct PaperWebPreview: NSViewRepresentable {
    let url: URL
    let mode: Int
    let scrollTarget: String?
    let jumpRequest: Int
    let findQuery: String
    let findRequest: Int
    let findBackwards: Bool
    @Binding var headings: [ReaderHeading]
    @Binding var currentAnchor: String?
    @Binding var findCount: Int

    final class Coordinator: NSObject, WKNavigationDelegate, WKScriptMessageHandler {
        var parent: PaperWebPreview
        var loadedURL: URL?
        var ready = false
        var appliedMode: Int?
        var lastJump = 0
        var lastFind = -1
        init(_ parent: PaperWebPreview) { self.parent = parent }
        func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
            guard let data = message.body as? [String: Any] else { return }
            if let items = data["headings"] as? [[String: Any]] {
                parent.headings = items.compactMap { item in
                    guard let id = item["id"] as? String, let title = item["title"] as? String, let level = item["level"] as? Int else { return nil }
                    return ReaderHeading(id: id, title: title, level: level)
                }
            }
            if let current = data["current"] as? String { parent.currentAnchor = current }
            if let anchor = data["anchor"] as? String, let offset = data["offset"] as? Double {
                UserDefaults.standard.set(["anchor": anchor, "offset": offset, "mode": parent.mode], forKey: ReaderProgressStore.key(parent.url))
            }
        }
        func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
            ready = true
            appliedMode = nil
            apply(webView)
            let progress = UserDefaults.standard.dictionary(forKey: ReaderProgressStore.key(parent.url)) ?? [:]
            let anchor = progress["anchor"] as? String ?? ""
            let offset = progress["offset"] as? Double ?? 0
            webView.evaluateJavaScript("window.kpaperReader.restore(\(Self.json(anchor)), \(offset));")
        }
        static func json(_ text: String) -> String {
            let data = try! JSONSerialization.data(withJSONObject: [text])
            return String(data: data, encoding: .utf8)!.dropFirst().dropLast().description
        }
        func apply(_ webView: WKWebView) {
            guard ready else { return }
            if appliedMode != parent.mode {
                appliedMode = parent.mode
                var progress = UserDefaults.standard.dictionary(forKey: ReaderProgressStore.key(parent.url)) ?? [:]
                progress["mode"] = parent.mode
                UserDefaults.standard.set(progress, forKey: ReaderProgressStore.key(parent.url))
                webView.evaluateJavaScript("window.kpaperReader.mode(\(parent.mode));")
            }
            if lastJump != parent.jumpRequest, let target = parent.scrollTarget {
                lastJump = parent.jumpRequest
                webView.evaluateJavaScript("window.kpaperReader.jump(\(Self.json(target)));")
            }
            if lastFind != parent.findRequest {
                lastFind = parent.findRequest
                let query = parent.findQuery
                webView.evaluateJavaScript("window.kpaperReader.count(\(Self.json(query)));") { result, _ in
                    DispatchQueue.main.async { self.parent.findCount = result as? Int ?? 0 }
                }
                let config = WKFindConfiguration()
                config.backwards = parent.findBackwards
                config.wraps = true
                webView.find(query, configuration: config) { _ in }
            }
        }
    }
    func makeCoordinator() -> Coordinator { Coordinator(self) }
    func makeNSView(context: Context) -> WKWebView {
        let controller = WKUserContentController()
        controller.add(context.coordinator, name: "reader")
        controller.addUserScript(WKUserScript(source: Self.bridge, injectionTime: .atDocumentEnd, forMainFrameOnly: true))
        let configuration = WKWebViewConfiguration()
        configuration.userContentController = controller
        let view = WKWebView(frame: .zero, configuration: configuration)
        view.navigationDelegate = context.coordinator
        view.setValue(false, forKey: "drawsBackground")
        return view
    }
    func updateNSView(_ view: WKWebView, context: Context) {
        context.coordinator.parent = self
        if context.coordinator.loadedURL != url {
            context.coordinator.loadedURL = url
            context.coordinator.ready = false
            context.coordinator.lastJump = jumpRequest
            context.coordinator.lastFind = -1
            view.loadFileURL(url, allowingReadAccessTo: url.deletingLastPathComponent().deletingLastPathComponent())
        } else { context.coordinator.apply(view) }
    }
    static func dismantleNSView(_ view: WKWebView, coordinator: Coordinator) {
        view.configuration.userContentController.removeScriptMessageHandler(forName: "reader")
        view.navigationDelegate = nil
    }

    static let bridge = #"""
    (() => {
      const post = data => window.webkit.messageHandlers.reader.postMessage(data);
      const style = document.createElement('style');
      style.textContent = `
        .codex_tab_button {display:none!important}
        .codex_tabs {min-height:0!important;padding:4px!important}
        .codex_tabs:has(.codex_sync_button[hidden]){display:none!important}
        body.has_bilingual_view .codex_parallel {grid-template-columns:minmax(0,1fr) minmax(0,1fr)!important;height:calc(100vh - 44px)!important;overflow:hidden!important}
        body.has_bilingual_view .codex_parallel_column {height:100%!important;overflow-y:auto!important;overflow-x:hidden!important;border-top:0!important;overscroll-behavior:contain;overflow-anchor:none}
        body.has_bilingual_view .codex_parallel .ltx_document {padding-left:18px!important;padding-right:18px!important}
        body.kpaper-native-parallel {overflow:hidden!important}
      `;
      document.head.append(style);
      let mode = 0, timer, restoring = false;
      const root = () => document.querySelector(mode === 1 ? '#codex-panel-parallel .codex_parallel_column:last-child article' : '#codex-panel-ko article') || document.querySelector('article');
      const headings = () => [...(root()?.querySelectorAll('h2,h3,h4,h5,h6') || [])].filter(e => !e.closest('details'));
      const ensureID = (e, i) => { if (!e.id) e.id = e.closest('[id]')?.id ? e.closest('[id]').id + '-reader-heading-' + i : 'reader-heading-' + i; return e.id; };
      const container = () => { const r=root(); const col=r?.closest('.codex_parallel_column'); return col && getComputedStyle(col).overflowY === 'auto' ? col : document.scrollingElement; };
      const top = e => e.getBoundingClientRect().top - (container() === document.scrollingElement ? 0 : container().getBoundingClientRect().top);
      function withNavigation(operation) {
        if(window.kpaperViewer?.performNavigation) return window.kpaperViewer.performNavigation(operation);
        // Older generated readers still need fresh scroll baselines after a jump.
        const button = document.querySelector('.codex_sync_button');
        const enabled = button?.getAttribute?.('aria-pressed') === 'true';
        if(enabled) button.click();
        try { operation(); } finally { if(enabled) button.click(); }
      }
      function report() {
        if(mode === 1) document.querySelectorAll('#codex-panel-parallel .codex_parallel_column article').forEach(r => {
          [...(r.querySelectorAll?.('h2,h3,h4,h5,h6') || [])].filter(e => !e.closest('details')).forEach(ensureID);
        });
        const list = headings();
        const items = list.map((e,i) => ({id:ensureID(e,i), title:e.textContent.trim(), level:Number(e.tagName.slice(1))}));
        const current = [...list].reverse().find(e => top(e) <= 90) || list[0];
        const anchors = [...(root()?.querySelectorAll('.ltx_para[id],figure[id],h2[id],h3[id],h4[id]') || [])].filter(e => e.getClientRects().length && !e.closest('details:not([open])'));
        const anchor = [...anchors].reverse().find(e => top(e) <= 80) || anchors[0];
        post({headings:items, current:current?.id || '', ...(!restoring && anchor ? {anchor:anchor.id, offset:top(anchor)} : {})});
      }
      window.kpaperReader = {
        mode(value) { value = value === 1 && document.querySelector('#codex-panel-parallel') ? 1 : 0; mode=value; document.body.classList.toggle('kpaper-native-parallel', value===1); document.querySelector('.codex_tab_button[data-target="'+(value===1?'codex-panel-parallel':'codex-panel-ko')+'"]')?.click(); if(value===1) document.scrollingElement.scrollTop=0; setTimeout(report,200); },
        jump(id) {
          const roots = mode === 1 ? [...document.querySelectorAll('#codex-panel-parallel .codex_parallel_column article')] : [root()];
          withNavigation(() => {
          for (const r of roots) {
            const e = r?.querySelector('#'+CSS.escape(id));
            if (!e) continue;
            // Citations may point into a collapsed references section.
            for (let ancestor=e.parentElement; ancestor; ancestor=ancestor.parentElement) {
              if (ancestor.tagName === 'DETAILS') ancestor.open = true;
            }
            const col = e.closest('.codex_parallel_column');
            if (mode === 1 && col) col.scrollTop += e.getBoundingClientRect().top-col.getBoundingClientRect().top-46;
            else e.scrollIntoView({block:'start'});
          }
          if(mode===1) document.scrollingElement.scrollTop=0;
          });
          setTimeout(report,100);
        },
        restore(id, offset) {
          if (!id) return;
          restoring = true;
          setTimeout(() => {
            const roots = mode === 1 ? [...document.querySelectorAll('#codex-panel-parallel .codex_parallel_column article')] : [root()];
            let found = false;
            withNavigation(() => {
            for (const r of roots) {
              const e = r?.querySelector('#'+CSS.escape(id));
              if (!e) continue;
              found = true;
              const col = e.closest('.codex_parallel_column');
              const c = col && getComputedStyle(col).overflowY === 'auto' ? col : document.scrollingElement;
              const y = e.getBoundingClientRect().top - (c === document.scrollingElement ? 0 : c.getBoundingClientRect().top);
              c.scrollTop += y-offset;
            }
            if(mode===1) document.scrollingElement.scrollTop=0;
            });
            report();
            if (found) setTimeout(() => { restoring=false; report(); },250);
            else ['wheel','keydown','pointerdown','touchstart'].forEach(type => document.addEventListener(type, () => { restoring=false; }, {once:true}));
          },300);
        },
        count(query) { if(!query) return 0; const text=(mode === 1 ? document.querySelector('#codex-panel-parallel') : root())?.innerText.toLocaleLowerCase() || ''; return text.split(query.toLocaleLowerCase()).length-1; }
      };
      document.addEventListener('scroll', () => { clearTimeout(timer); timer=setTimeout(report,180); }, true);
      window.addEventListener('resize', () => { clearTimeout(timer); timer=setTimeout(report,200); });
      document.addEventListener('toggle', () => setTimeout(report,100), true);
      window.addEventListener('pagehide', report);
    })();
    """#
}
