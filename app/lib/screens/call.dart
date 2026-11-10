import 'dart:async';

import 'package:flutter/material.dart';

import '../api.dart';
import '../state.dart';
import '../strings.dart';
import 'onboarding.dart';

/// One screen for the whole life of a consultation: searching, the call itself, and the summary after.
///
/// [request] is the body that was sent to POST /match. It is kept so "Next doctor" can search again
/// with the same filters. It is null when the screen is opened from history or after an app restart.
class CallScreen extends StatefulWidget {
  const CallScreen({super.key, required this.consultation, required this.request});

  final Map<String, dynamic> consultation;
  final Map<String, dynamic>? request;

  @override
  State<CallScreen> createState() => _CallScreenState();
}

class _CallScreenState extends State<CallScreen> {
  late Map<String, dynamic> _c;
  Timer? _poller;
  Timer? _ticker;
  bool _busy = false;
  bool _polling = false;
  bool _joining = false;
  int _stars = 0;

  String get _id => _c['id'] as String;

  String get _status => _c['status'] as String;

  bool get _finished => const <String>['ended', 'skipped', 'cancelled', 'no_doctor'].contains(_status);

  @override
  void initState() {
    super.initState();
    _c = widget.consultation;
    _poller = Timer.periodic(const Duration(seconds: 2), (Timer timer) => _poll());
    _ticker = Timer.periodic(const Duration(seconds: 1), (Timer timer) {
      if (mounted && _status == 'active') setState(() {});
    });
    WidgetsBinding.instance.addPostFrameCallback((Duration _) => _joinIfReady());
  }

  /// A doctor picked up our waiting request: answer straight away so the doctor is not kept waiting.
  Future<void> _joinIfReady() async {
    if (_joining || !mounted || _status != 'ready') return;
    _joining = true;
    try {
      final Map<String, dynamic> started = await appState.api.post('/consultations/$_id/join');
      if (mounted) setState(() => _c = started);
    } on ApiException catch (e) {
      if (mounted && e.code != 'not_ready') showMessage(context, errorText(e));
    } finally {
      _joining = false;
    }
  }

  @override
  void dispose() {
    _poller?.cancel();
    _ticker?.cancel();
    super.dispose();
  }

  /// Ask the server for the latest state. The server is the only judge of time and money.
  Future<void> _poll({bool force = false}) async {
    if (_polling || (_finished && !force)) return;
    _polling = true;
    try {
      final Map<String, dynamic> latest = await appState.api.get('/consultations/$_id');
      if (mounted && latest['id'] == _id) setState(() => _c = latest);
      _joinIfReady();
    } on ApiException catch (_) {
      // A failed poll is retried two seconds later.
    } finally {
      _polling = false;
    }
  }

  Future<void> _run(Future<void> Function() action) async {
    if (_busy) return;
    setState(() => _busy = true);
    try {
      await action();
    } catch (e) {
      if (mounted) showMessage(context, errorText(e));
    }
    if (mounted) setState(() => _busy = false);
  }

  Future<void> _cancelSearch() => _run(() async {
        try {
          await appState.api.post('/consultations/$_id/cancel');
        } on ApiException catch (e) {
          if (e.code != 'cannot_cancel') rethrow; // a doctor accepted at the same moment
          await _poll(force: true);
          return;
        }
        if (mounted) Navigator.of(context).pop();
      });

  Future<void> _endCall({required bool thenNext}) => _run(() async {
        try {
          final Map<String, dynamic> ended = await appState.api.post('/consultations/$_id/end');
          if (mounted) setState(() => _c = ended);
        } on ApiException catch (e) {
          if (e.code != 'not_active') rethrow; // the call had already ended
          await _poll(force: true);
        }
        if (thenNext) await _searchAgain();
      });

  Future<void> _searchAgain() async {
    final Map<String, dynamic>? request = widget.request;
    if (request == null) return;
    final Map<String, dynamic> next = await appState.api.post('/match', request);
    if (mounted) {
      setState(() {
        _c = next;
        _stars = 0;
      });
    }
  }

  Future<void> _rate(int stars) => _run(() async {
        final Map<String, dynamic> rated =
            await appState.api.post('/consultations/$_id/rating', <String, dynamic>{'rating': stars});
        if (mounted) {
          setState(() => _c = rated);
          showMessage(context, t('rated'));
        }
      });

  Future<void> _report() async {
    final String? reason = await showModalBottomSheet<String>(
      context: context,
      builder: (BuildContext context) => SafeArea(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: <Widget>[
            ListTile(title: Text(t('report_title'), style: const TextStyle(fontWeight: FontWeight.bold))),
            for (final String r in <String>['abuse', 'prescribed_medicine', 'inappropriate', 'fake_doctor', 'other'])
              ListTile(title: Text(t('report_$r')), onTap: () => Navigator.of(context).pop(r)),
          ],
        ),
      ),
    );
    if (reason == null) return;
    await _run(() async {
      await appState.api.post('/consultations/$_id/report', <String, dynamic>{'reason': reason});
      if (mounted) showMessage(context, t('report_sent'));
    });
  }

  @override
  Widget build(BuildContext context) {
    // A waiting request keeps waiting on the server, so the patient may leave this screen.
    final bool inProgress = !_finished && _status != 'waiting';
    return PopScope(
      canPop: !inProgress, // the back button must not silently leave a search or a paid call running
      child: Scaffold(
        appBar: AppBar(automaticallyImplyLeading: !inProgress, title: Text(t('app_name'))),
        body: SafeArea(
          child: Padding(
            padding: const EdgeInsets.all(24),
            child: switch (_status) {
              'searching' || 'offered' => _searchingView(),
              'waiting' => _waitingView(),
              'ready' => _readyView(),
              'active' => _activeView(),
              _ => _finishedView(),
            },
          ),
        ),
      ),
    );
  }

  // --------------------------------------------------------------------------- searching

  Widget _searchingView() {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: <Widget>[
        const Spacer(),
        const Center(child: SizedBox(width: 72, height: 72, child: CircularProgressIndicator(strokeWidth: 6))),
        const SizedBox(height: 32),
        Text(
          t(_status == 'offered' ? 'ringing' : 'searching'),
          textAlign: TextAlign.center,
          style: const TextStyle(fontSize: 22, fontWeight: FontWeight.w600),
        ),
        const SizedBox(height: 12),
        // Money is held only for a fresh search; a waiting request holds nothing until the doctor accepts.
        if (_c['from_waiting_request'] != true)
          Text(
            t('searching_note').replaceAll('{price}', taka(_c['price_taka'] as num)),
            textAlign: TextAlign.center,
          ),
        const Spacer(),
        OutlinedButton(
          style: OutlinedButton.styleFrom(minimumSize: const Size.fromHeight(56)),
          onPressed: _busy ? null : _cancelSearch,
          child: Text(t('stop_search')),
        ),
      ],
    );
  }

  // --------------------------------------------------------------------------- waiting request

  Widget _waitingView() {
    final dynamic expiresAt = _c['expires_at'];
    String until = '';
    if (expiresAt is num) {
      final DateTime d = DateTime.fromMillisecondsSinceEpoch((expiresAt * 1000).round());
      until = '${d.hour.toString().padLeft(2, '0')}:${d.minute.toString().padLeft(2, '0')}';
    }
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: <Widget>[
        Expanded(
          child: ListView(
            children: <Widget>[
              const SizedBox(height: 24),
              Icon(Icons.hourglass_top, size: 72, color: Theme.of(context).colorScheme.primary),
              const SizedBox(height: 24),
              Text(t('waiting_title'), textAlign: TextAlign.center, style: const TextStyle(fontSize: 22, fontWeight: FontWeight.w600)),
              const SizedBox(height: 16),
              Text(t('waiting_body'), style: const TextStyle(fontSize: 16)),
              if (until.isNotEmpty)
                Padding(
                  padding: const EdgeInsets.only(top: 16),
                  child: Text(t('waiting_until').replaceAll('{time}', until)),
                ),
            ],
          ),
        ),
        FilledButton(onPressed: () => Navigator.of(context).pop(), child: Text(t('waiting_keep'))),
        const SizedBox(height: 8),
        OutlinedButton(
          style: OutlinedButton.styleFrom(minimumSize: const Size.fromHeight(56)),
          onPressed: _busy ? null : _cancelSearch,
          child: Text(t('waiting_cancel')),
        ),
      ],
    );
  }

  Widget _readyView() {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: <Widget>[
        Text(t('ready_title'), style: const TextStyle(fontSize: 22, fontWeight: FontWeight.bold)),
        const SizedBox(height: 16),
        _doctorCard(),
        const Spacer(),
        const Center(child: CircularProgressIndicator()),
        const SizedBox(height: 16),
        Text(t('ready_body'), textAlign: TextAlign.center),
        const Spacer(),
        FilledButton(onPressed: _joinIfReady, child: Text(t('join_now'))),
      ],
    );
  }

  // --------------------------------------------------------------------------- the call

  Widget _doctorCard() {
    final dynamic doctor = _c['doctor'];
    if (doctor is! Map) return const SizedBox.shrink();
    final String lang = appState.lang;
    final List<String> details = <String>[
      designationName(lang, doctor['designation'] as String?),
      specialtyName(lang, doctor['specialty'] as String),
      t('years').replaceAll('{years}', doctor['experience_years'].toString()),
    ].where((String s) => s.isNotEmpty).toList();
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        Text(doctor['name'] as String, style: const TextStyle(fontSize: 22, fontWeight: FontWeight.bold)),
        const SizedBox(height: 4),
        Row(
          children: <Widget>[
            Icon(Icons.verified, size: 18, color: Theme.of(context).colorScheme.primary),
            const SizedBox(width: 6),
            Expanded(
              child: Text(
                '${t('verified')} · ${t('bmdc').replaceAll('{number}', doctor['bmdc_number'] as String)}',
              ),
            ),
          ],
        ),
        const SizedBox(height: 4),
        Text(details.join(' · ')),
      ],
    );
  }

  Widget _activeView() {
    final double now = DateTime.now().millisecondsSinceEpoch / 1000;
    final double startedAt = (_c['started_at'] as num).toDouble();
    final int secondsLeft = ((_c['ends_at'] as num).toDouble() - now).ceil();
    final int freeSeconds = (_c['free_skip_seconds'] as num).toInt();
    final bool skipIsFree = now - startedAt <= freeSeconds;
    final bool canSearchAgain = widget.request != null;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: <Widget>[
        _doctorCard(),
        const SizedBox(height: 16),
        Expanded(
          child: Container(
            decoration: BoxDecoration(color: Colors.black87, borderRadius: BorderRadius.circular(16)),
            alignment: Alignment.center,
            padding: const EdgeInsets.all(24),
            // The video picture goes here once the video service is connected (see README).
            child: Text(t('video_pending'), textAlign: TextAlign.center, style: const TextStyle(color: Colors.white70)),
          ),
        ),
        const SizedBox(height: 12),
        Row(
          mainAxisAlignment: MainAxisAlignment.spaceBetween,
          children: <Widget>[
            Text('${t('time_left')}: ${clock(secondsLeft)}', style: const TextStyle(fontSize: 18, fontWeight: FontWeight.w600)),
            TextButton.icon(onPressed: _report, icon: const Icon(Icons.flag_outlined), label: Text(t('report'))),
          ],
        ),
        if (skipIsFree)
          Padding(
            padding: const EdgeInsets.only(bottom: 8),
            child: Text(
              t('free_skip').replaceAll('{seconds}', freeSeconds.toString()),
              style: TextStyle(color: Theme.of(context).colorScheme.primary),
            ),
          ),
        Row(
          children: <Widget>[
            Expanded(
              child: FilledButton(
                style: FilledButton.styleFrom(backgroundColor: Colors.red.shade700),
                onPressed: _busy ? null : () => _endCall(thenNext: false),
                child: Text(t('end_call')),
              ),
            ),
            if (canSearchAgain) ...<Widget>[
              const SizedBox(width: 12),
              Expanded(
                child: FilledButton(
                  onPressed: _busy ? null : () => _endCall(thenNext: true),
                  child: Text(t('next_doctor')),
                ),
              ),
            ],
          ],
        ),
      ],
    );
  }

  // --------------------------------------------------------------------------- after the call

  Widget _adviceNote() {
    final dynamic note = _c['advice_note'];
    if (note is! Map) {
      return Text(t('advice_waiting'));
    }
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        for (final String field in <String>['assessment', 'self_care', 'tests', 'referral'])
          if ((note[field] as String? ?? '').isNotEmpty)
            Padding(
              padding: const EdgeInsets.only(bottom: 12),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: <Widget>[
                  Text(t(field), style: const TextStyle(fontWeight: FontWeight.w600)),
                  Text(note[field] as String),
                ],
              ),
            ),
      ],
    );
  }

  Widget _finishedView() {
    final String status = _status;
    final bool connected = status == 'ended' || status == 'skipped';
    final num charged = (_c['price_charged_taka'] ?? 0) as num;
    final int rating = ((_c['rating'] ?? _stars) as num).toInt();
    final bool alreadyRated = _c['rating'] != null;

    String headline = t('call_ended');
    String detail = '';
    if (status == 'no_doctor') {
      headline = t('no_doctor');
    } else if (status == 'cancelled') {
      headline = t('request_cancelled');
    } else if (status == 'skipped') {
      detail = t('not_charged');
    } else if (status == 'ended') {
      detail = t('charged').replaceAll('{amount}', taka(charged));
    }

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: <Widget>[
        Expanded(
          child: ListView(
            children: <Widget>[
              Text(headline, style: const TextStyle(fontSize: 22, fontWeight: FontWeight.bold)),
              if (detail.isNotEmpty) Padding(padding: const EdgeInsets.only(top: 4), child: Text(detail)),
              const SizedBox(height: 20),
              if (connected) _doctorCard(),
              if (status == 'ended') ...<Widget>[
                const SizedBox(height: 20),
                Text(t('rate_doctor'), style: const TextStyle(fontWeight: FontWeight.w600)),
                Row(
                  children: <Widget>[
                    for (int star = 1; star <= 5; star++)
                      IconButton(
                        iconSize: 36,
                        onPressed: alreadyRated || _busy
                            ? null
                            : () {
                                setState(() => _stars = star);
                                _rate(star);
                              },
                        icon: Icon(
                          star <= rating ? Icons.star : Icons.star_border,
                          color: Colors.amber.shade700,
                        ),
                      ),
                  ],
                ),
                const SizedBox(height: 12),
                Row(
                  children: <Widget>[
                    Expanded(child: Text(t('advice_note'), style: const TextStyle(fontSize: 18, fontWeight: FontWeight.bold))),
                    IconButton(tooltip: t('refresh'), onPressed: () => _poll(force: true), icon: const Icon(Icons.refresh)),
                  ],
                ),
                _adviceNote(),
              ],
              if (connected)
                Align(
                  alignment: Alignment.centerLeft,
                  child: TextButton.icon(onPressed: _report, icon: const Icon(Icons.flag_outlined), label: Text(t('report'))),
                ),
            ],
          ),
        ),
        if (widget.request != null)
          Padding(
            padding: const EdgeInsets.only(bottom: 8),
            child: FilledButton(
              onPressed: _busy ? null : () => _run(_searchAgain),
              child: Text('${t('next_doctor')} · ${taka(appState.callPrice)}'),
            ),
          ),
        OutlinedButton(
          style: OutlinedButton.styleFrom(minimumSize: const Size.fromHeight(56)),
          onPressed: () => Navigator.of(context).pop(),
          child: Text(t('home')),
        ),
      ],
    );
  }
}
