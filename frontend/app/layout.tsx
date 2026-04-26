import type { Metadata } from 'next';
import './styles.css';
import DisclaimerBanner from './components/DisclaimerBanner';
import DisclaimerModal from './components/DisclaimerModal';

export const metadata: Metadata = {
  title: 'Order Intake Agent — reads order emails, refuses to write bad ones',
  description:
    'AI agent for B2B order intake. Reads every PO, validates each line, writes only the clean ones. Built for Google Solution Hackathon.',
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link
          rel="preconnect"
          href="https://fonts.gstatic.com"
          crossOrigin="anonymous"
        />
        <link
          href="https://fonts.googleapis.com/css2?family=Jost:ital,wght@0,300..800;1,300..800&family=Azeret+Mono:ital,wght@0,300..700;1,300..700&family=Instrument+Serif:ital@0;1&display=swap"
          rel="stylesheet"
        />
      </head>
      <body>
        <DisclaimerBanner />
        {children}
        <DisclaimerModal />
      </body>
    </html>
  );
}
