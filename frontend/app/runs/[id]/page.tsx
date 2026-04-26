import { notFound } from 'next/navigation';
import { getRunIds, loadRun } from '@/app/lib/loadRun';
import ReadSheet from '@/app/components/ReadSheet';

export function generateStaticParams() {
  return getRunIds().map((id) => ({ id }));
}

export default function RunSheetPage({ params }: { params: { id: string } }) {
  let data;
  try {
    data = loadRun(params.id);
  } catch {
    notFound();
  }
  return <ReadSheet id={params.id} data={data} />;
}
