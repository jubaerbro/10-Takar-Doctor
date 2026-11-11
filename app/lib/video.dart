import 'dart:async';

import 'package:flutter/material.dart';
import 'package:livekit_client/livekit_client.dart';

import 'state.dart';

/// The live video for one call: the other person full-size, yourself in a corner, and
/// buttons for microphone and camera.
///
/// [video] is what the server sent: {"url", "room", "token"}. It is null when the server has
/// no video service configured, and then a plain notice is shown instead.
/// The widget connects once when it appears and disconnects when it is removed.
class VideoPanel extends StatefulWidget {
  const VideoPanel({super.key, required this.video});

  final Map<String, dynamic>? video;

  @override
  State<VideoPanel> createState() => _VideoPanelState();
}

class _VideoPanelState extends State<VideoPanel> {
  Room? _room;
  EventsListener<RoomEvent>? _listener;
  bool _connecting = false;
  bool _failed = false;
  bool _micOn = true;
  bool _cameraOn = true;

  @override
  void initState() {
    super.initState();
    _connect();
  }

  @override
  void dispose() {
    final Room? room = _room;
    _listener?.dispose();
    if (room != null) {
      // Leaving the screen ends our side of the call: stop the camera and microphone at once.
      unawaited(room.disconnect().then((_) => room.dispose()));
    }
    super.dispose();
  }

  Future<void> _connect() async {
    final Map<String, dynamic>? video = widget.video;
    if (video == null || _connecting) return;
    setState(() {
      _connecting = true;
      _failed = false;
    });
    final Room room = Room(roomOptions: const RoomOptions(adaptiveStream: true, dynacast: true));
    try {
      await room.connect(video['url'] as String, video['token'] as String);
      if (!mounted) {
        await room.disconnect();
        await room.dispose();
        return;
      }
      // Redraw whenever anything in the room changes: someone joins, a camera turns on or off, and so on.
      _listener = room.createListener()..on<RoomEvent>((RoomEvent event) {
        if (mounted) setState(() {});
      });
      _room = room;
      try {
        await Hardware.instance.setSpeakerphoneOn(true);
      } catch (_) {
        // Not every device lets us choose the speaker; the call still works through the earpiece.
      }
      await _apply(microphone: true, camera: true);
    } catch (_) {
      await room.dispose();
      if (mounted) setState(() => _failed = true);
    }
    if (mounted) setState(() => _connecting = false);
  }

  /// Turn our microphone and camera on or off. If the phone refuses (permission denied, no camera),
  /// the button shows it as off and the call carries on with what is available.
  Future<void> _apply({bool? microphone, bool? camera}) async {
    final LocalParticipant? me = _room?.localParticipant;
    if (me == null) return;
    if (microphone != null) {
      try {
        await me.setMicrophoneEnabled(microphone);
        _micOn = microphone;
      } catch (_) {
        _micOn = false;
      }
    }
    if (camera != null) {
      try {
        await me.setCameraEnabled(camera);
        _cameraOn = camera;
      } catch (_) {
        _cameraOn = false;
      }
    }
    if (mounted) setState(() {});
  }

  VideoTrack? _remoteVideo() {
    final Room? room = _room;
    if (room == null) return null;
    for (final RemoteParticipant participant in room.remoteParticipants.values) {
      for (final RemoteTrackPublication<RemoteVideoTrack> publication in participant.videoTrackPublications) {
        final RemoteVideoTrack? track = publication.track;
        if (track != null && publication.subscribed && !publication.muted) return track;
      }
    }
    return null;
  }

  VideoTrack? _localVideo() {
    final LocalParticipant? me = _room?.localParticipant;
    if (me == null || !_cameraOn) return null;
    for (final LocalTrackPublication<LocalVideoTrack> publication in me.videoTrackPublications) {
      final LocalVideoTrack? track = publication.track;
      if (track != null && !publication.muted) return track;
    }
    return null;
  }

  Widget _notice(String text, {Widget? action}) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(24),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: <Widget>[
            Text(text, textAlign: TextAlign.center, style: const TextStyle(color: Colors.white70, fontSize: 16)),
            if (action != null) Padding(padding: const EdgeInsets.only(top: 12), child: action),
          ],
        ),
      ),
    );
  }

  Widget _roundButton({required IconData icon, required bool on, required String tooltip, required VoidCallback onPressed}) {
    return IconButton.filled(
      tooltip: tooltip,
      style: IconButton.styleFrom(
        backgroundColor: on ? Colors.white24 : Colors.red.shade700,
        foregroundColor: Colors.white,
        minimumSize: const Size(52, 52),
      ),
      onPressed: onPressed,
      icon: Icon(icon),
    );
  }

  @override
  Widget build(BuildContext context) {
    final Room? room = _room;
    Widget content;
    if (widget.video == null) {
      content = _notice(t('video_pending'));
    } else if (_failed) {
      content = _notice(t('video_failed'), action: FilledButton.tonal(onPressed: _connect, child: Text(t('retry'))));
    } else if (room == null) {
      content = _notice(t('video_connecting'));
    } else {
      final VideoTrack? remote = _remoteVideo();
      final VideoTrack? local = _localVideo();
      final bool otherSideHere = room.remoteParticipants.isNotEmpty;
      content = Stack(
        fit: StackFit.expand,
        children: <Widget>[
          if (remote != null)
            VideoTrackRenderer(remote)
          else
            _notice(t(otherSideHere ? 'video_other_camera_off' : 'video_waiting_other')),
          if (local != null)
            Positioned(
              top: 12,
              right: 12,
              width: 96,
              height: 128,
              child: ClipRRect(borderRadius: BorderRadius.circular(10), child: VideoTrackRenderer(local)),
            ),
          Positioned(
            left: 0,
            right: 0,
            bottom: 12,
            child: Row(
              mainAxisAlignment: MainAxisAlignment.center,
              children: <Widget>[
                _roundButton(
                  icon: _micOn ? Icons.mic : Icons.mic_off,
                  on: _micOn,
                  tooltip: t('video_mic'),
                  onPressed: () => _apply(microphone: !_micOn),
                ),
                const SizedBox(width: 16),
                _roundButton(
                  icon: _cameraOn ? Icons.videocam : Icons.videocam_off,
                  on: _cameraOn,
                  tooltip: t('video_camera'),
                  onPressed: () => _apply(camera: !_cameraOn),
                ),
              ],
            ),
          ),
        ],
      );
    }
    return ClipRRect(
      borderRadius: BorderRadius.circular(16),
      child: ColoredBox(color: Colors.black87, child: content),
    );
  }
}
