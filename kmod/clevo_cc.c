// SPDX-License-Identifier: GPL-2.0+
/*
 * clevo_cc.c - Clevo Control Center hardware bridge
 *
 * A minimal char device (/dev/clevo_cc) that lets a userspace daemon call
 * the Clevo firmware control interface used by the OEM "Control Center":
 *   - ACPI _DSM interface on device "CLV0001"
 *     (UUID 93f224e4-fbdc-4bbf-add6-db71bdc0afad, revision 0, func = command)
 *   - WMI method interface, GUID ABBC0F6D-8EA1-11D1-00A0-C90629100000
 *
 * Protocol on /dev/clevo_cc:
 *   write 8 bytes:  struct cc_req  { __u32 cmd; __u32 arg; }
 *   read 16 bytes:  struct cc_res  { __s32 status; __u32 result;
 *                                    __u32 via;    __u32 version; }
 *   read 64 bytes:  raw last buffer reply (e.g. CLEVO_CMD_GET_SPECS 0x0d)
 *
 * Special commands (cmd >= 0xFFFF0000):
 *   0xFFFF0001  get module version
 *   0xFFFF0002  get active interface (0=none, 1=ACPI DSM, 2=WMI)
 *   0xFFFF0003  re-probe interfaces
 *   0xFFFF0004  arg = force interface (0 auto, 1 acpi, 2 wmi)
 *
 * Based on knowledge from tuxedo-drivers (GPL-2.0+),
 * Copyright (c) 2020-2026 TUXEDO Computers GmbH <tux@tuxedocomputers.com>
 */

#define pr_fmt(fmt) KBUILD_MODNAME ": " fmt

#include <linux/module.h>
#include <linux/kernel.h>
#include <linux/miscdevice.h>
#include <linux/fs.h>
#include <linux/uaccess.h>
#include <linux/mutex.h>
#include <linux/delay.h>
#include <linux/acpi.h>
#include <linux/wmi.h>
#include <linux/types.h>
#include <linux/string.h>

#define CC_DEVICE_NAME		"clevo_cc"
#define CC_MODULE_VERSION	0x00010000
#define CC_LBUF_SIZE		64

#define CLEVO_ACPI_HID		"CLV0001"
#define CLEVO_ACPI_DSM_UUID	"93f224e4-fbdc-4bbf-add6-db71bdc0afad"
#define CLEVO_WMI_METHOD_GUID	"ABBC0F6D-8EA1-11D1-00A0-C90629100000"

#define CLEVO_CMD_GET_BIOS_FEATURES_1	0x52
#define CLEVO_CMD_SET_FANSPEED_VALUE	0x68
#define CLEVO_CMD_SET_FANSPEED_AUTO	0x69

#define CC_IF_NONE		0
#define CC_IF_ACPI		1
#define CC_IF_WMI		2

#define CC_MAGIC_BASE		0xFFFF0000u
#define CC_MAGIC_VERSION	(CC_MAGIC_BASE + 1)
#define CC_MAGIC_GET_IF		(CC_MAGIC_BASE + 2)
#define CC_MAGIC_REPROBE	(CC_MAGIC_BASE + 3)
#define CC_MAGIC_FORCE_IF	(CC_MAGIC_BASE + 4)

/* module parameter: 0 = auto, 1 = force ACPI DSM, 2 = force WMI */
static int interface = 0;
module_param(interface, int, 0444);
MODULE_PARM_DESC(interface, "Preferred interface: 0 auto, 1 ACPI DSM, 2 WMI");

static bool debug;
module_param(debug, bool, 0644);
MODULE_PARM_DESC(debug, "Log every firmware call");

struct cc_req {
	__u32 cmd;
	__u32 arg;
};

struct cc_res {
	__s32 status;
	__u32 result;
	__u32 via;
	__u32 version;
};

static struct acpi_device *cc_adev;
static int cc_active = CC_IF_NONE;
static DEFINE_MUTEX(cc_mutex);
static struct cc_res cc_last;
static u8 cc_lbuf[CC_LBUF_SIZE];
static u32 cc_llen;

static void cc_save_buf(const u8 *src, u32 len)
{
	cc_llen = min_t(u32, len, CC_LBUF_SIZE);
	memset(cc_lbuf, 0, sizeof(cc_lbuf));
	memcpy(cc_lbuf, src, cc_llen);
}

/* ---- raw transport calls ---------------------------------------------- */

static int cc_do_acpi(u32 cmd, u32 arg, u32 *out)
{
	guid_t uuid;
	union acpi_object *res;
	u64 dsm_rev = 0;
	union acpi_object argv_data = {
		.integer.type = ACPI_TYPE_INTEGER,
		.integer.value = arg,
	};
	union acpi_object argv_pkg = {
		.package.type = ACPI_TYPE_PACKAGE,
		.package.count = 1,
		.package.elements = &argv_data,
	};

	if (!cc_adev)
		return -ENODEV;
	if (guid_parse(CLEVO_ACPI_DSM_UUID, &uuid) < 0)
		return -EINVAL;

	res = acpi_evaluate_dsm(acpi_device_handle(cc_adev), &uuid, dsm_rev,
				(u64)cmd, &argv_pkg);
	if (!res)
		return -EIO;

	if (res->type == ACPI_TYPE_INTEGER) {
		*out = (u32)res->integer.value;
		ACPI_FREE(res);
		return 0;
	}
	if (res->type == ACPI_TYPE_BUFFER && res->buffer.length >= 4) {
		*out = *(u32 *)res->buffer.pointer;
		cc_save_buf(res->buffer.pointer, res->buffer.length);
		ACPI_FREE(res);
		return 0;
	}
	ACPI_FREE(res);
	return -EBADMSG;
}

static int cc_do_wmi(u32 cmd, u32 arg, u32 *out)
{
	struct acpi_buffer in = { sizeof(arg), &arg };
	struct acpi_buffer outb = { ACPI_ALLOCATE_BUFFER, NULL };
	union acpi_object *res;
	acpi_status st;

	if (!wmi_has_guid(CLEVO_WMI_METHOD_GUID))
		return -ENODEV;

	st = wmi_evaluate_method(CLEVO_WMI_METHOD_GUID, 0, cmd, &in, &outb);
	if (ACPI_FAILURE(st))
		return -EIO;

	res = (union acpi_object *)outb.pointer;
	if (!res)
		return -EFAULT;
	if (res->type == ACPI_TYPE_INTEGER) {
		*out = (u32)res->integer.value;
		ACPI_FREE(outb.pointer);
		return 0;
	}
	if (res->type == ACPI_TYPE_BUFFER && res->buffer.length >= 4) {
		*out = *(u32 *)res->buffer.pointer;
		cc_save_buf(res->buffer.pointer, res->buffer.length);
		ACPI_FREE(outb.pointer);
		return 0;
	}
	ACPI_FREE(outb.pointer);
	return -EBADMSG;
}

static int cc_call(u32 cmd, u32 arg, u32 *out)
{
	int st;

	cc_llen = 0;
	switch (cc_active) {
	case CC_IF_ACPI:
		st = cc_do_acpi(cmd, arg, out);
		break;
	case CC_IF_WMI:
		st = cc_do_wmi(cmd, arg, out);
		break;
	default:
		return -ENODEV;
	}
	if (debug)
		pr_info("call cmd=%#04x arg=%#010x -> st=%d result=%#010x\n",
			cmd, arg, st, *out);
	if (!st && (cmd == CLEVO_CMD_SET_FANSPEED_VALUE ||
		    cmd == CLEVO_CMD_SET_FANSPEED_AUTO))
		msleep(100);
	return st;
}

/* ---- interface probing ------------------------------------------------ */

static bool cc_probe_acpi(void)
{
	u32 v = 0;

	if (!cc_adev)
		return false;
	if (cc_do_acpi(CLEVO_CMD_GET_BIOS_FEATURES_1, 0, &v))
		return false;
	if (v == 0xffffffff)
		return false;
	return true;
}

static bool cc_probe_wmi(void)
{
	u32 v = 0;

	if (cc_do_wmi(CLEVO_CMD_GET_BIOS_FEATURES_1, 0, &v))
		return false;
	if (v == 0xffffffff)
		return false;
	return true;
}

static void cc_probe(bool verbose)
{
	int forced = READ_ONCE(interface);

	if (!cc_adev) {
		cc_adev = acpi_dev_get_first_match_dev(CLEVO_ACPI_HID, NULL, -1);
		if (cc_adev)
			pr_info("found ACPI device %s\n", CLEVO_ACPI_HID);
	}

	cc_active = CC_IF_NONE;

	if (forced == 0 || forced == 1) {
		if (cc_probe_acpi())
			cc_active = CC_IF_ACPI;
	}
	if (cc_active == CC_IF_NONE && (forced == 0 || forced == 2)) {
		if (cc_probe_wmi())
			cc_active = CC_IF_WMI;
	}
	if (verbose || cc_active != CC_IF_NONE)
		pr_info("active interface: %s\n",
			cc_active == CC_IF_ACPI ? "ACPI(CLV0001 DSM)" :
			cc_active == CC_IF_WMI ? "WMI(ABBC0F6D)" : "none");
}

/* ---- char device ------------------------------------------------------ */

static ssize_t cc_write(struct file *f, const char __user *buf, size_t count,
			loff_t *pos)
{
	struct cc_req req;
	u32 out = 0;
	int st = 0;

	if (count != sizeof(req))
		return -EINVAL;
	if (copy_from_user(&req, buf, sizeof(req)))
		return -EFAULT;

	mutex_lock(&cc_mutex);
	switch (req.cmd) {
	case CC_MAGIC_VERSION:
		out = CC_MODULE_VERSION;
		break;
	case CC_MAGIC_GET_IF:
		out = (u32)cc_active;
		break;
	case CC_MAGIC_REPROBE:
		cc_probe(true);
		out = (u32)cc_active;
		break;
	case CC_MAGIC_FORCE_IF:
		if (req.arg <= 2) {
			interface = (int)req.arg;
			cc_probe(true);
			out = (u32)cc_active;
		} else {
			st = -EINVAL;
		}
		break;
	default:
		if (req.cmd >= CC_MAGIC_BASE)
			st = -EINVAL;
		else
			st = cc_call(req.cmd, req.arg, &out);
		break;
	}

	cc_last.status = st;
	cc_last.result = out;
	cc_last.via = (u32)cc_active;
	cc_last.version = CC_MODULE_VERSION;
	mutex_unlock(&cc_mutex);

	if (st < 0)
		return st;
	return (ssize_t)count;
}

static ssize_t cc_read(struct file *f, char __user *buf, size_t count,
		       loff_t *pos)
{
	struct cc_res res;

	if (count >= CC_LBUF_SIZE) {
		u8 tmp[CC_LBUF_SIZE];

		mutex_lock(&cc_mutex);
		memcpy(tmp, cc_lbuf, CC_LBUF_SIZE);
		mutex_unlock(&cc_mutex);
		if (copy_to_user(buf, tmp, CC_LBUF_SIZE))
			return -EFAULT;
		return (ssize_t)CC_LBUF_SIZE;
	}

	if (count < sizeof(res))
		return -EINVAL;

	mutex_lock(&cc_mutex);
	res = cc_last;
	mutex_unlock(&cc_mutex);

	if (copy_to_user(buf, &res, sizeof(res)))
		return -EFAULT;
	return (ssize_t)sizeof(res);
}

static const struct file_operations cc_fops = {
	.owner = THIS_MODULE,
	.write = cc_write,
	.read = cc_read,
};

static struct miscdevice cc_miscdev = {
	.minor = MISC_DYNAMIC_MINOR,
	.name = CC_DEVICE_NAME,
	.fops = &cc_fops,
	.mode = 0600,
};

/* ---- module init/exit ------------------------------------------------- */

static int __init cc_init(void)
{
	int st;

	st = misc_register(&cc_miscdev);
	if (st) {
		pr_err("failed to register misc device\n");
		return st;
	}

	cc_probe(true);
	pr_info("module loaded (version %08x)\n", CC_MODULE_VERSION);
	return 0;
}

static void __exit cc_exit(void)
{
	misc_deregister(&cc_miscdev);
	if (cc_adev) {
		acpi_dev_put(cc_adev);
		cc_adev = NULL;
	}
	pr_info("module unloaded\n");
}

module_init(cc_init);
module_exit(cc_exit);

MODULE_AUTHOR("Control Center bridge");
MODULE_DESCRIPTION("Clevo Control Center interface bridge (fan/keyboard/leds)");
MODULE_LICENSE("GPL");
MODULE_ALIAS("acpi*:" CLEVO_ACPI_HID ":*");
MODULE_ALIAS("wmi:" CLEVO_WMI_METHOD_GUID);
