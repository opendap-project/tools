//! A uinput direct-touch device (`linux/uinput.h`, `linux/input-event-codes.h`).

use std::{fs::OpenOptions, io, os::fd::AsRawFd, thread::sleep, time::Duration};

const EV_SYN: u16 = 0;
const EV_KEY: u16 = 1;
const EV_ABS: u16 = 3;
const SYN_REPORT: u16 = 0;
const BTN_TOUCH: u16 = 0x14a;
const ABS_X: u16 = 0x00;
const ABS_Y: u16 = 0x01;
const ABS_MT_SLOT: u16 = 0x2f;
const ABS_MT_POSITION_X: u16 = 0x35;
const ABS_MT_POSITION_Y: u16 = 0x36;
const ABS_MT_TRACKING_ID: u16 = 0x39;
const INPUT_PROP_DIRECT: libc::c_ulong = 0x01;
const BUS_VIRTUAL: u16 = 0x06;

// _IO / _IOW('U', n, …) on Linux.
const UI_DEV_CREATE: libc::c_ulong = 0x5501;
const UI_DEV_DESTROY: libc::c_ulong = 0x5502;
const UI_DEV_SETUP: libc::c_ulong = 0x405c_5503;
const UI_ABS_SETUP: libc::c_ulong = 0x401c_5504;
const UI_SET_EVBIT: libc::c_ulong = 0x4004_5564;
const UI_SET_KEYBIT: libc::c_ulong = 0x4004_5565;
const UI_SET_ABSBIT: libc::c_ulong = 0x4004_5567;
const UI_SET_PROPBIT: libc::c_ulong = 0x4004_556e;

#[repr(C)]
struct InputId {
    bustype: u16,
    vendor: u16,
    product: u16,
    version: u16,
}

#[repr(C)]
struct UinputSetup {
    id: InputId,
    name: [u8; 80],
    ff_effects_max: u32,
}

#[repr(C)]
struct AbsInfo {
    value: i32,
    minimum: i32,
    maximum: i32,
    fuzz: i32,
    flat: i32,
    resolution: i32,
}

#[repr(C)]
struct UinputAbsSetup {
    code: u16,
    absinfo: AbsInfo,
}

#[repr(C)]
struct InputEvent {
    time: libc::timeval,
    kind: u16,
    code: u16,
    value: i32,
}

fn ioctl(fd: i32, request: libc::c_ulong, arg: libc::c_ulong) -> io::Result<()> {
    // Safety: every request here takes an integer or a pointer to a live, correctly laid out struct.
    if unsafe { libc::ioctl(fd, request as _, arg) } < 0 {
        return Err(io::Error::last_os_error());
    }
    Ok(())
}

fn emit(fd: i32, kind: u16, code: u16, value: i32) -> io::Result<()> {
    let ev = InputEvent { time: libc::timeval { tv_sec: 0, tv_usec: 0 }, kind, code, value };
    let size = std::mem::size_of::<InputEvent>();
    // Safety: `ev` is a plain repr(C) struct of `size` bytes.
    let n = unsafe { libc::write(fd, (&ev as *const InputEvent).cast(), size) };
    if n != size as isize {
        return Err(io::Error::last_os_error());
    }
    Ok(())
}

fn frame(fd: i32, x: i32, y: i32) -> io::Result<()> {
    emit(fd, EV_ABS, ABS_MT_POSITION_X, x)?;
    emit(fd, EV_ABS, ABS_MT_POSITION_Y, y)?;
    emit(fd, EV_ABS, ABS_X, x)?;
    emit(fd, EV_ABS, ABS_Y, y)?;
    emit(fd, EV_SYN, SYN_REPORT, 0)
}

/// One contact along `points`, spread over `duration_ms`, on a panel of `size` pixels.
pub fn contact(size: (i32, i32), points: &[(i32, i32)], duration_ms: i32) -> io::Result<()> {
    let file = OpenOptions::new().write(true).open("/dev/uinput")?;
    let fd = file.as_raw_fd();

    ioctl(fd, UI_SET_EVBIT, EV_KEY as _)?;
    ioctl(fd, UI_SET_EVBIT, EV_ABS as _)?;
    ioctl(fd, UI_SET_KEYBIT, BTN_TOUCH as _)?;
    ioctl(fd, UI_SET_PROPBIT, INPUT_PROP_DIRECT)?;
    for (code, max) in [
        (ABS_X, size.0 - 1),
        (ABS_Y, size.1 - 1),
        (ABS_MT_POSITION_X, size.0 - 1),
        (ABS_MT_POSITION_Y, size.1 - 1),
        (ABS_MT_SLOT, 9),
        (ABS_MT_TRACKING_ID, 65535),
    ] {
        ioctl(fd, UI_SET_ABSBIT, code as _)?;
        let setup = UinputAbsSetup {
            code,
            absinfo: AbsInfo { value: 0, minimum: 0, maximum: max, fuzz: 0, flat: 0, resolution: 0 },
        };
        ioctl(fd, UI_ABS_SETUP, &setup as *const _ as _)?;
    }
    let mut name = [0u8; 80];
    let label = b"odap-inject touch";
    name[..label.len()].copy_from_slice(label);
    let setup = UinputSetup {
        id: InputId { bustype: BUS_VIRTUAL, vendor: 0, product: 0, version: 1 },
        name,
        ff_effects_max: 0,
    };
    ioctl(fd, UI_DEV_SETUP, &setup as *const _ as _)?;
    ioctl(fd, UI_DEV_CREATE, 0)?;
    // Let udev tag the device and the compositor's libinput open it.
    sleep(Duration::from_millis(800));

    let result = (|| {
        let (x0, y0) = points[0];
        emit(fd, EV_ABS, ABS_MT_SLOT, 0)?;
        emit(fd, EV_ABS, ABS_MT_TRACKING_ID, 1)?;
        emit(fd, EV_KEY, BTN_TOUCH, 1)?;
        frame(fd, x0, y0)?;
        let step = Duration::from_millis((duration_ms.max(1) as u64) / points.len().max(1) as u64);
        for &(x, y) in &points[1..] {
            sleep(step);
            frame(fd, x, y)?;
        }
        sleep(step);
        emit(fd, EV_ABS, ABS_MT_TRACKING_ID, -1)?;
        emit(fd, EV_KEY, BTN_TOUCH, 0)?;
        emit(fd, EV_SYN, SYN_REPORT, 0)
    })();

    sleep(Duration::from_millis(200));
    let _ = ioctl(fd, UI_DEV_DESTROY, 0);
    result
}
