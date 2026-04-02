"""Main orchestration system for Polymarket Pricing Gap Detection."""

import asyncio
import time
from datetime import datetime
from pathlib import Path

from crewai import Crew, Process

from .agents import (
    DataCollectionAgent,
    SentimentAnalysisAgent,
    GapDetectionAgent,
    ReportingAgent
)
from .config import get_settings
from .database import init_database, get_db_manager
from .database.models import CycleRun
from .utils.logger import setup_logger, get_logger


class PolymarketGapDetector:
    """
    Main orchestration class for the multi-agent pricing gap detection system.

    Coordinates four specialized agents:
    1. Data Collection Agent - Fetches market and social media data
    2. Sentiment Analysis Agent - Analyzes social sentiment
    3. Gap Detection Agent - Identifies pricing inefficiencies
    4. Reporting Agent - Ranks and formats results
    """

    def __init__(self):
        """Initialize the gap detection system."""
        # Setup logger
        self.logger = setup_logger()
        self.logger.info("Initializing Polymarket Gap Detector...")

        # Load settings
        self.settings = get_settings()

        # Initialize database
        try:
            self.db_manager = init_database()
            self.logger.info("Database connection established")
        except Exception as e:
            self.logger.error(f"Failed to initialize database: {e}")
            raise

        # Initialize agents
        self.data_collector = DataCollectionAgent()
        self.sentiment_analyzer = SentimentAnalysisAgent()
        self.gap_detector = GapDetectionAgent()
        self.reporter = ReportingAgent()

        self.cycle_count = 0
        self.logger.info("All agents initialized successfully")

    def run_single_cycle(self) -> dict:
        """
        Execute one complete analysis cycle.

        Returns:
            Dictionary with cycle results
        """
        self.cycle_count += 1
        cycle_start = time.time()
        cycle_started_at = datetime.utcnow()
        self.logger.info("=" * 80)
        self.logger.info(f"STARTING ANALYSIS CYCLE #{self.cycle_count}")
        self.logger.info("=" * 80)

        results = {
            'timestamp': datetime.utcnow().isoformat(),
            'success': False,
            'errors': []
        }

        timeout = self.settings.cycle_timeout
        last_completed_phase = "none"

        def _timed_out():
            elapsed = time.time() - cycle_start
            return elapsed > timeout

        try:
            # Step 1: Data Collection
            self.logger.info("\n[STEP 1/4] Data Collection")
            self.logger.info("-" * 80)
            collection_results = self.data_collector.run()
            results['collection'] = {
                'contracts_collected': len(collection_results.get('contracts', [])),
                'social_posts': sum(len(p) for p in collection_results.get('social_posts', {}).values())
            }
            self.logger.info(f"✓ Collected {results['collection']['contracts_collected']} contracts, "
                           f"{results['collection']['social_posts']} social posts")
            last_completed_phase = "data_collection"

            if _timed_out():
                self.logger.warning(
                    f"Cycle timeout ({timeout}s) reached after data collection "
                    f"({time.time() - cycle_start:.1f}s elapsed) — skipping remaining phases"
                )
                results['timed_out'] = True
                results['last_completed_phase'] = last_completed_phase
                raise TimeoutError("cycle_timeout")

            # Step 2: Sentiment Analysis
            self.logger.info("\n[STEP 2/4] Sentiment Analysis")
            self.logger.info("-" * 80)
            sentiment_results = self.sentiment_analyzer.run()
            results['sentiment'] = {
                'contracts_analyzed': len(sentiment_results)
            }
            self.logger.info(f"✓ Analyzed sentiment for {len(sentiment_results)} contracts")
            last_completed_phase = "sentiment_analysis"

            if _timed_out():
                self.logger.warning(
                    f"Cycle timeout ({timeout}s) reached after sentiment analysis "
                    f"({time.time() - cycle_start:.1f}s elapsed) — skipping remaining phases"
                )
                results['timed_out'] = True
                results['last_completed_phase'] = last_completed_phase
                raise TimeoutError("cycle_timeout")

            # Step 3: Gap Detection
            self.logger.info("\n[STEP 3/4] Gap Detection")
            self.logger.info("-" * 80)
            gaps = self.gap_detector.run()
            results['gaps'] = {
                'total_gaps': len(gaps),
                'by_type': {}
            }

            # Count gaps by type
            for gap in gaps:
                gap_type = gap.get('gap_type', 'unknown')
                results['gaps']['by_type'][gap_type] = results['gaps']['by_type'].get(gap_type, 0) + 1

            self.logger.info(f"✓ Detected {len(gaps)} pricing gaps")
            last_completed_phase = "gap_detection"

            if _timed_out():
                self.logger.warning(
                    f"Cycle timeout ({timeout}s) reached after gap detection "
                    f"({time.time() - cycle_start:.1f}s elapsed) — skipping remaining phases"
                )
                results['timed_out'] = True
                results['last_completed_phase'] = last_completed_phase
                raise TimeoutError("cycle_timeout")

            # Step 4.5: Backtesting (optional)
            if self.settings.enable_backtesting:
                try:
                    self.logger.info("\n[STEP 4.5] Running Backtest")
                    self.logger.info("-" * 80)
                    from .analysis import Backtester
                    backtester = Backtester()
                    backtest = backtester.run_backtest(
                        confidence_threshold=self.settings.min_confidence_score,
                    )
                    results['backtest'] = {
                        'total_predictions': backtest.get('total_predictions', 0),
                        'win_rate': backtest.get('win_rate', 0),
                    }
                    self.logger.info(f"✓ Backtest: {backtest.get('total_predictions', 0)} predictions, "
                                     f"win rate {backtest.get('win_rate', 0):.1%}")
                    last_completed_phase = "backtesting"
                except Exception as e:
                    self.logger.warning(f"Backtest skipped: {e}")

                if _timed_out():
                    self.logger.warning(
                        f"Cycle timeout ({timeout}s) reached after backtesting "
                        f"({time.time() - cycle_start:.1f}s elapsed) — skipping reporting phase"
                    )
                    results['timed_out'] = True
                    results['last_completed_phase'] = last_completed_phase
                    raise TimeoutError("cycle_timeout")

            # Step 5: Reporting
            self.logger.info("\n[STEP 5/5] Generating Report")
            self.logger.info("-" * 80)
            ranked_gaps = self.reporter.run()
            results['report'] = {
                'gaps_reported': len(ranked_gaps)
            }
            last_completed_phase = "reporting"

            results['success'] = True
            cycle_duration = time.time() - cycle_start
            results['duration_seconds'] = round(cycle_duration, 2)

            self.logger.info("\n" + "=" * 80)
            self.logger.info(f"CYCLE COMPLETE - Duration: {cycle_duration:.2f}s")
            self.logger.info("=" * 80)

        except TimeoutError:
            # Cycle timeout — partial results already populated above
            cycle_duration = time.time() - cycle_start
            results['duration_seconds'] = round(cycle_duration, 2)
            results['success'] = False
            results['errors'].append(
                f"Cycle timed out after {cycle_duration:.0f}s (limit: {timeout}s). "
                f"Last completed phase: {last_completed_phase}"
            )
            self.logger.warning(
                f"Cycle #{self.cycle_count} timed out after {cycle_duration:.1f}s — "
                f"last completed phase: {last_completed_phase}"
            )
        except Exception as e:
            self.logger.error(f"Error during analysis cycle: {e}", exc_info=True)
            results['errors'].append(str(e))

        # Save cycle run to database
        try:
            db = get_db_manager()
            with db.get_session() as session:
                cycle_run = CycleRun(
                    cycle_number=self.cycle_count,
                    started_at=cycle_started_at,
                    finished_at=datetime.utcnow(),
                    duration_seconds=results.get('duration_seconds', round(time.time() - cycle_start, 2)),
                    success=results.get('success', False),
                    contracts_collected=results.get('collection', {}).get('contracts_collected', 0),
                    posts_collected=results.get('collection', {}).get('social_posts', 0),
                    sentiments_analyzed=results.get('sentiment', {}).get('contracts_analyzed', 0),
                    gaps_detected=results.get('gaps', {}).get('total_gaps', 0),
                    llm_provider=self.settings.llm_provider,
                    errors=results.get('errors') if results.get('errors') else None,
                    cycle_metadata={
                        'gaps_by_type': results.get('gaps', {}).get('by_type', {}),
                        'backtest': results.get('backtest', {}),
                    },
                )
                session.add(cycle_run)
                session.commit()
                self.logger.info(f"Cycle #{self.cycle_count} saved to history")
        except Exception as e:
            self.logger.warning(f"Failed to save cycle history: {e}")

        return results

    def run_continuous(self):
        """
        Run continuous monitoring with polling intervals.

        This is the main entry point for production use.
        """
        self.logger.info("Starting continuous monitoring mode")
        self.logger.info(f"Polling interval: {self.settings.polling_interval} seconds")

        cycle_count = 0

        try:
            while True:
                cycle_count += 1
                self.logger.info(f"\n{'='*80}")
                self.logger.info(f"CYCLE #{cycle_count}")
                self.logger.info(f"{'='*80}\n")

                # Run analysis cycle
                results = self.run_single_cycle()

                if results['success']:
                    self.logger.info(f"✓ Cycle #{cycle_count} completed successfully")
                else:
                    self.logger.error(f"✗ Cycle #{cycle_count} completed with errors")

                # Wait for next cycle
                self.logger.info(f"\nWaiting {self.settings.polling_interval} seconds until next cycle...")
                time.sleep(self.settings.polling_interval)

        except KeyboardInterrupt:
            self.logger.info("\n\nShutdown signal received. Stopping gracefully...")
            self.cleanup()
        except Exception as e:
            self.logger.error(f"Fatal error in continuous mode: {e}", exc_info=True)
            self.cleanup()
            raise

    def cleanup(self):
        """Cleanup resources on shutdown."""
        self.logger.info("Cleaning up resources...")

        # Close database connections
        if hasattr(self, 'db_manager'):
            self.db_manager.close()
            self.logger.info("Database connections closed")

        self.logger.info("Cleanup complete. Goodbye!")

    def run_demo(self):
        """
        Run a single demonstration cycle with detailed output.

        Useful for testing and demonstrations.
        """
        self.logger.info("=" * 80)
        self.logger.info("RUNNING DEMONSTRATION MODE")
        self.logger.info("=" * 80)
        self.logger.info("")

        results = self.run_single_cycle()

        if results['success']:
            self.logger.info("\n✓ Demo completed successfully!")
            self.logger.info(f"   - Collected {results['collection']['contracts_collected']} contracts")
            self.logger.info(f"   - Analyzed {results['collection']['social_posts']} social posts")
            self.logger.info(f"   - Detected {results['gaps']['total_gaps']} pricing gaps")
            self.logger.info(f"   - Duration: {results['duration_seconds']}s")
        else:
            self.logger.error("\n✗ Demo completed with errors")
            for error in results.get('errors', []):
                self.logger.error(f"   - {error}")


def main():
    """Main entry point."""
    import sys

    # Create logs directory if it doesn't exist
    Path("logs").mkdir(exist_ok=True)

    # Parse command line arguments
    mode = sys.argv[1] if len(sys.argv) > 1 else "continuous"

    try:
        detector = PolymarketGapDetector()

        if mode == "demo":
            detector.run_demo()
        elif mode == "once":
            detector.run_single_cycle()
        elif mode == "dashboard":
            from .dashboard.app import start_dashboard
            start_dashboard()
        else:
            detector.run_continuous()

    except KeyboardInterrupt:
        print("\n\nExiting...")
    except Exception as e:
        print(f"Fatal error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
