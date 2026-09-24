import Link from "next/link";

export default function NotFound() {
  return (
    <>
      <h1>Not found</h1>
      <p className="lede">
        That page does not exist, or the record it referred to has not been published.
      </p>
      <p><Link href="/applications">Browse all applications →</Link></p>
    </>
  );
}
