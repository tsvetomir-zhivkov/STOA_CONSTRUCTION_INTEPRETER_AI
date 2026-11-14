/**
 * Tiny XML reader sufficient for SVG: elements, attributes, text. No entity expansion beyond the five predefined
 * entities and numeric references (so no billion-laughs / XXE surface), DOCTYPE and processing instructions are skipped.
 */

/** @typedef {{ name:string, attrs:Record<string,string>, children:XmlNode[], text:string, parent:XmlNode|null }} XmlNode */

const decode = (s) => s
  .replace(/&#x([0-9a-fA-F]+);/g, (_, h) => String.fromCodePoint(parseInt(h, 16)))
  .replace(/&#(\d+);/g, (_, d) => String.fromCodePoint(Number(d)))
  .replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"').replace(/&apos;/g, "'").replace(/&amp;/g, '&');

/** @param {string} xml @returns {XmlNode} synthetic root */
export function parseXml(xml) {
  const root = { name: '#root', attrs: {}, children: [], text: '', parent: null };
  let cur = root;
  const re = /<!--[\s\S]*?-->|<\?[\s\S]*?\?>|<!DOCTYPE[^>[]*(?:\[[\s\S]*?\])?\s*>|<!\[CDATA\[([\s\S]*?)\]\]>|<(\/?)([A-Za-z_][\w:.-]*)((?:\s+[^\s=/>]+(?:\s*=\s*(?:"[^"]*"|'[^']*'))?)*)\s*(\/?)>|([^<]+)/g;
  let m;
  while ((m = re.exec(xml))) {
    if (m[1] !== undefined) { cur.text += m[1]; continue; }
    if (m[3]) {
      const name = m[3].replace(/^.*:/, '').toLowerCase();
      if (m[2]) { // closing tag
        let n = cur;
        while (n && n.name !== name) n = n.parent;
        if (n?.parent) cur = n.parent;
      } else {
        const attrs = {};
        const ar = /([^\s=/>]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'))?/g;
        let a;
        while ((a = ar.exec(m[4]))) attrs[a[1].replace(/^.*:/, '')] = decode(a[2] ?? a[3] ?? '');
        const node = { name, attrs, children: [], text: '', parent: cur };
        cur.children.push(node);
        if (!m[5]) cur = node;
      }
    } else if (m[6] !== undefined) cur.text += decode(m[6]);
  }
  return root;
}
