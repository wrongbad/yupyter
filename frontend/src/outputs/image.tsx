export function ImageOutput({ mime, data }: { mime: string; data: string }) {
  if (mime === "image/svg+xml") {
    return <div class="out-image" dangerouslySetInnerHTML={{ __html: data }} />;
  }
  const b64 = String(data).replace(/\s/g, "");
  return (
    <div class="out-image">
      <img src={`data:${mime};base64,${b64}`} alt="output" />
    </div>
  );
}
