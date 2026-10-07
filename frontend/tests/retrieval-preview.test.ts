import { describe, expect, it } from "vitest";
import { referencePath, verifyPreview } from "../src/retrieval-preview";
import type { Hit } from "../src/retrieval-types";
const id = "11111111-1111-1111-1111-111111111111";
const hit: Hit = {
  rank: 1,
  evidence_id: `lab:${id}:0`,
  kind: "log",
  product_version: "1.1",
  text: "",
  cosine_similarity: null,
  source: {
    experiment_id: id,
    run_id: id,
    ordinal: 0,
    sha256: "digest",
    event: { event: "<script>danger</script>", status: 504 },
  },
  reference_url: `/api/experiments/${id}`,
};
describe("REQ-705/708 固定锚点预览", () => {
  it("拒绝外部路径和负数观测", () => {
    expect(referencePath(hit)).toBe(`/api/experiments/${id}`);
    expect(() =>
      referencePath({ ...hit, reference_url: "https://example.com" }),
    ).toThrow();
    expect(() =>
      referencePath({ ...hit, source: { ...hit.source, ordinal: -1 } }),
    ).toThrow();
  });
  it("核对原包版本和事件，保留文本而不解析 HTML", () => {
    const data = {
      text_verified: true,
      run_id: id,
      sha256: "digest",
      product_version: "1.1",
      evidence_ids: [hit.evidence_id],
      artifact: {
        observations: [{ status: 504, event: "<script>danger</script>" }],
      },
    };
    expect(verifyPreview(hit, data)).toEqual({
      kind: "log",
      event: hit.source.event,
    });
    expect(() =>
      verifyPreview(hit, { ...data, product_version: "2.0" }),
    ).toThrow();
    expect(() => verifyPreview(hit, { ...data, sha256: "changed" })).toThrow();
    expect(() =>
      verifyPreview(hit, {
        ...data,
        artifact: { observations: [{ status: 200 }] },
      }),
    ).toThrow();
  });
});
